"""Docling-based parser: PDF -> structured document with elements + images.

We pull out:
  - text elements with reading-order index, page number, bbox, role (Title/Heading/...)
  - picture elements with bbox + image bytes saved to disk

Why Docling: best layout + table fidelity for born-digital PDFs as of 2026,
gives us the bboxes we need for the image-binding cascade.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from PIL import Image

from ragqa.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class ParsedElement:
    """One text-bearing element from the document (paragraph, heading, list item, table)."""
    elem_id: str
    role: str               # Title / SectionHeader / Paragraph / ListItem / Table / Caption
    text: str
    page: int
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    reading_order: int
    level: int = 0          # heading level when role is SectionHeader


@dataclass
class ParsedImage:
    """One picture from the document, materialized to disk."""
    image_id: str
    file_path: Path
    page: int
    bbox: tuple[float, float, float, float]
    reading_order: int
    width: int
    height: int


@dataclass
class ParsedDocument:
    doc_id: str
    source_path: Path
    pages: int
    elements: list[ParsedElement] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)
    parser_version: str = ""


def _bbox_of(prov) -> tuple[float, float, float, float]:
    """Extract a (x0, y0, x1, y1) bbox from a docling provenance entry."""
    if prov and getattr(prov, "bbox", None):
        b = prov.bbox
        return (float(b.l), float(b.t), float(b.r), float(b.b))
    return (0.0, 0.0, 0.0, 0.0)


class DoclingParser:
    """Wrap docling.DocumentConverter with our domain types."""

    def __init__(self, images_dir: Path):
        self.images_dir = images_dir
        self.images_dir.mkdir(parents=True, exist_ok=True)

        opts = PdfPipelineOptions()
        # 1.0 = PDF-native resolution. Sufficient for VLM (gpt-4o downsamples to
        # 768/2048 anyway). Higher scales blow memory on large docs (QAman: 608 pp).
        opts.images_scale = 1.0
        opts.generate_picture_images = True
        opts.generate_page_images = False  # we don't need full-page renders, only pictures
        opts.do_ocr = False              # source is born-digital, OCR adds noise
        opts.do_table_structure = True
        opts.table_structure_options.do_cell_matching = True

        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

        try:
            import docling
            self.parser_version = f"docling-{docling.__version__}"
        except Exception:
            self.parser_version = "docling-unknown"

    def parse(self, pdf_path: Path, doc_id: str) -> ParsedDocument:
        log.info("docling.parse.start", doc_id=doc_id, pdf=str(pdf_path))
        result = self._converter.convert(str(pdf_path))
        dl_doc = result.document

        elements: list[ParsedElement] = []
        order = 0

        # Text elements (paragraphs, headings, list items, captions)
        for item in dl_doc.texts:
            prov = item.prov[0] if item.prov else None
            if prov is None:
                continue
            page = int(getattr(prov, "page_no", 0)) or 0
            role = item.label.value if hasattr(item.label, "value") else str(item.label)
            text = (item.text or "").strip()
            if not text:
                continue
            level = 0
            if role.lower() in ("section_header", "title"):
                level = int(getattr(item, "level", 1) or 1)
            elements.append(ParsedElement(
                elem_id=f"{doc_id}_e{order:05d}",
                role=role,
                text=text,
                page=page,
                bbox=_bbox_of(prov),
                reading_order=order,
                level=level,
            ))
            order += 1

        # Tables — rendered as text with markdown-ish layout
        for tbl in getattr(dl_doc, "tables", []) or []:
            prov = tbl.prov[0] if tbl.prov else None
            if prov is None:
                continue
            try:
                table_text = tbl.export_to_markdown()
            except Exception:
                table_text = ""
            if not table_text.strip():
                continue
            elements.append(ParsedElement(
                elem_id=f"{doc_id}_t{order:05d}",
                role="Table",
                text=table_text,
                page=int(getattr(prov, "page_no", 0)) or 0,
                bbox=_bbox_of(prov),
                reading_order=order,
            ))
            order += 1

        # Pictures — save bytes to disk, index by id
        images: list[ParsedImage] = []
        for idx, pic in enumerate(getattr(dl_doc, "pictures", []) or []):
            prov = pic.prov[0] if pic.prov else None
            if prov is None:
                continue
            try:
                pil_img = pic.get_image(dl_doc)
            except Exception as e:
                log.warning("docling.image.skip", reason=repr(e), idx=idx)
                continue
            if pil_img is None:
                continue

            img_id = self._image_id(doc_id, pil_img, idx)
            out_path = self.images_dir / doc_id / f"{img_id}.png"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not out_path.exists():
                pil_img.save(out_path, format="PNG", optimize=True)

            images.append(ParsedImage(
                image_id=img_id,
                file_path=out_path,
                page=int(getattr(prov, "page_no", 0)) or 0,
                bbox=_bbox_of(prov),
                reading_order=order,
                width=pil_img.width,
                height=pil_img.height,
            ))
            order += 1

        # Sort elements by reading order
        elements.sort(key=lambda e: e.reading_order)
        images.sort(key=lambda i: i.reading_order)

        n_pages = len(dl_doc.pages) if hasattr(dl_doc, "pages") else 0
        log.info("docling.parse.done", doc_id=doc_id,
                 elements=len(elements), images=len(images), pages=n_pages)

        return ParsedDocument(
            doc_id=doc_id,
            source_path=pdf_path,
            pages=n_pages,
            elements=elements,
            images=images,
            parser_version=self.parser_version,
        )

    @staticmethod
    def _image_id(doc_id: str, pil_img: Image.Image, idx: int) -> str:
        """Stable image_id = doc_id + sha256(image bytes) prefix.
        Same image extracted twice gets the same id (so cache + binding stay stable)."""
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        h = hashlib.sha256(buf.getvalue()).hexdigest()[:12]
        return f"{doc_id}_img_{idx:04d}_{h}"
