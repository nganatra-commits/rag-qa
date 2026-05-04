"""PyMuPDF-based parser - torch-free fallback for Docling.

Produces the SAME ParsedDocument shape (elements + images), so binder.py,
chunker.py, captioner.py, and the rest of the pipeline are unchanged.

Trade-offs vs Docling:
  - We lose RT-DETR layout detection (use font-size heuristics for headings)
  - We lose Docling's table-structure model (PyMuPDF's find_tables() instead)
  + No torch dependency, no model download, ~10x faster, no Windows OOM
  + Works reliably on born-digital PDFs from MS Word (which is what we have)
"""
from __future__ import annotations

import hashlib
import io
from collections import Counter
from pathlib import Path

import pymupdf  # type: ignore[import-untyped]
from PIL import Image

from ragqa.core.logging import get_logger
from ragqa.ingestion.parser_types import ParsedDocument, ParsedElement, ParsedImage

log = get_logger(__name__)


class PyMuPDFParser:
    """Drop-in replacement for DoclingParser using PyMuPDF (fitz).

    Reading order, bbox, role, and image extraction all come from PyMuPDF's
    text-dict and image extraction APIs.
    """

    def __init__(self, images_dir: Path):
        self.images_dir = images_dir
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.parser_version = f"pymupdf-{pymupdf.__version__}"

    def parse(self, pdf_path: Path, doc_id: str) -> ParsedDocument:
        log.info("pymupdf.parse.start", doc_id=doc_id, pdf=str(pdf_path))
        doc = pymupdf.open(pdf_path)

        # First pass: gather all font sizes to discover heading thresholds
        font_sizes = Counter()
        for page in doc:
            blocks = page.get_text("dict").get("blocks", [])
            for blk in blocks:
                if blk.get("type") != 0:  # 0 = text, 1 = image
                    continue
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        size = round(span.get("size", 0), 1)
                        if size > 0:
                            font_sizes[size] += len(span.get("text", ""))
        body_size, heading_thresholds = _classify_sizes(font_sizes)
        log.info("pymupdf.fonts", body_size=body_size,
                 heading_thresholds=heading_thresholds, distinct=len(font_sizes))

        elements: list[ParsedElement] = []
        images: list[ParsedImage] = []
        order = 0
        seen_image_xrefs: dict[int, str] = {}  # xref -> image_id

        for page_idx, page in enumerate(doc, start=1):
            page_dict = page.get_text("dict")
            blocks = page_dict.get("blocks", [])
            blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))  # top-down, then left

            for blk in blocks:
                btype = blk.get("type", 0)
                bbox = tuple(float(x) for x in blk["bbox"])

                if btype == 1:  # image block
                    img_id = self._extract_image(
                        doc, page, blk, doc_id, page_idx, seen_image_xrefs
                    )
                    if img_id is None:
                        continue
                    pil_w, pil_h = blk.get("width", 0), blk.get("height", 0)
                    images.append(ParsedImage(
                        image_id=img_id,
                        file_path=self.images_dir / doc_id / f"{img_id}.png",
                        page=page_idx,
                        bbox=bbox,
                        reading_order=order,
                        width=int(pil_w) or 0,
                        height=int(pil_h) or 0,
                    ))
                    order += 1
                    continue

                # Text block: stitch all spans into one paragraph and pick a role
                text, max_size, dominant_size = _flatten_block_text(blk)
                if not text.strip():
                    continue

                role, level = _classify_role(
                    max_size, dominant_size, body_size, heading_thresholds, text
                )
                elements.append(ParsedElement(
                    elem_id=f"{doc_id}_e{order:05d}",
                    role=role,
                    text=text.strip(),
                    page=page_idx,
                    bbox=bbox,
                    reading_order=order,
                    level=level,
                ))
                order += 1

            # Tables - emit as their own Table elements (atomic chunks downstream)
            try:
                tables = page.find_tables()
                for tbl in tables.tables if hasattr(tables, "tables") else tables:
                    md = _table_to_markdown(tbl)
                    if not md.strip():
                        continue
                    bb = tbl.bbox if hasattr(tbl, "bbox") else (0.0, 0.0, 0.0, 0.0)
                    elements.append(ParsedElement(
                        elem_id=f"{doc_id}_t{order:05d}",
                        role="Table",
                        text=md,
                        page=page_idx,
                        bbox=tuple(float(x) for x in bb),
                        reading_order=order,
                    ))
                    order += 1
            except Exception as e:
                log.debug("pymupdf.tables.skip", page=page_idx, err=repr(e))

        n_pages = doc.page_count
        doc.close()
        log.info("pymupdf.parse.done", doc_id=doc_id,
                 elements=len(elements), images=len(images), pages=n_pages)
        return ParsedDocument(
            doc_id=doc_id,
            source_path=pdf_path,
            pages=n_pages,
            elements=elements,
            images=images,
            parser_version=self.parser_version,
        )

    def _extract_image(
        self,
        doc,
        page,
        block: dict,
        doc_id: str,
        page_idx: int,
        seen: dict[int, str],
    ) -> str | None:
        """Pull image bytes for the given block, save as PNG, return image_id.

        Reuses xref-keyed cache so duplicated screenshots get one file."""
        # PyMuPDF text-dict image blocks contain the raw image bytes already
        img_bytes: bytes | None = block.get("image")
        if not img_bytes:
            return None
        try:
            pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception as e:
            log.warning("pymupdf.image.decode_fail", err=repr(e))
            return None

        # Skip near-trivial images (likely decorative line art / logos)
        if pil.width < 32 or pil.height < 32:
            return None

        h = hashlib.sha256(img_bytes).hexdigest()[:12]
        img_id = f"{doc_id}_img_{page_idx:04d}_{h}"
        out = self.images_dir / doc_id / f"{img_id}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            pil.save(out, format="PNG", optimize=True)
        return img_id


# --- Heuristics -----------------------------------------------------------

def _classify_sizes(
    sizes: Counter[float],
) -> tuple[float, list[float]]:
    """Return (body_size, [h1, h2, h3]) thresholds.

    body_size = the size with most character coverage.
    Heading sizes = unique sizes strictly larger than body, ordered desc, top 3.
    """
    if not sizes:
        return 10.0, []
    body_size = sizes.most_common(1)[0][0]
    larger = sorted({s for s in sizes if s > body_size + 0.5}, reverse=True)
    return body_size, larger[:3]


def _flatten_block_text(block: dict) -> tuple[str, float, float]:
    """Return (text, max_font_size, dominant_font_size) for a text block."""
    parts: list[str] = []
    sizes: list[tuple[float, int]] = []  # (size, char count)
    max_size = 0.0
    for line in block.get("lines", []):
        line_parts: list[str] = []
        for span in line.get("spans", []):
            txt = span.get("text", "")
            if not txt:
                continue
            line_parts.append(txt)
            sz = round(span.get("size", 0), 1)
            sizes.append((sz, len(txt)))
            max_size = max(max_size, sz)
        if line_parts:
            parts.append("".join(line_parts))
    text = "\n".join(parts)
    dominant = max(sizes, key=lambda x: x[1])[0] if sizes else 0.0
    return text, max_size, dominant


def _classify_role(
    max_size: float,
    dominant_size: float,
    body_size: float,
    heading_thresholds: list[float],
    text: str,
) -> tuple[str, int]:
    """Return (role, heading_level)."""
    stripped = text.strip()
    # Caption: leading "Figure X" / "Table X" pattern
    low = stripped.lower()
    if low.startswith(("figure ", "fig.", "table ")) and len(stripped) < 200:
        return "Caption", 0
    # List items: bullet marker or numbered
    if stripped[:2] in ("• ", "- ", "* ") or _is_numbered_list_start(stripped):
        return "ListItem", 0
    # Headings via font size
    for i, threshold in enumerate(heading_thresholds, start=1):
        if dominant_size >= threshold - 0.2:
            return ("Title" if i == 1 else "SectionHeader"), i
    return "Paragraph", 0


def _is_numbered_list_start(text: str) -> bool:
    if len(text) < 3:
        return False
    head = text.split(maxsplit=1)[0]
    return head.rstrip(".)").isdigit() and head.endswith((".", ")"))


def _table_to_markdown(tbl) -> str:
    """Render a PyMuPDF Table as a simple Markdown table."""
    try:
        rows = tbl.extract()
    except Exception:
        return ""
    if not rows:
        return ""
    # Treat first row as header
    header = rows[0]
    body = rows[1:]
    lines = []
    lines.append("| " + " | ".join(_clean_cell(c) for c in header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for r in body:
        lines.append("| " + " | ".join(_clean_cell(c) for c in r) + " |")
    return "\n".join(lines)


def _clean_cell(c) -> str:
    if c is None:
        return ""
    return str(c).replace("\n", " ").replace("|", "/").strip()
