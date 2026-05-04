"""Image -> text binding cascade.

For every image extracted from a PDF, decide which text element(s) it
belongs to. Strongest binding first, fall back if not available.

Bindings are emitted as (elem_id, image_id, method, score) edges.
The chunker later resolves these to chunk-level binding when elements
are merged into chunks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ragqa.core.logging import get_logger
from ragqa.ingestion.parser_types import ParsedDocument, ParsedElement, ParsedImage
from ragqa.models.chunks import BindingMethod

log = get_logger(__name__)


# "Figure 4.2", "Fig. 1", "Screenshot 3", "see image below", etc.
_REF_PATTERN = re.compile(
    r"\b(?:figure|fig\.?|screenshot|image|picture|chart|diagram|table)\s*"
    r"(?:\d+(?:[.\-]\d+)*[a-z]?)?\b",
    re.IGNORECASE,
)
_DEICTIC_PATTERN = re.compile(
    r"\b(?:as shown (?:above|below)|see (?:above|below|the (?:figure|image|screenshot)))\b",
    re.IGNORECASE,
)


@dataclass
class Binding:
    elem_id: str
    image_id: str
    method: BindingMethod
    score: float


class ImageBinder:
    """Resolve image <-> text-element bindings via the four-rule cascade."""

    def __init__(self, vertical_window: float = 80.0):
        # vertical_window: how close (in PDF units) the nearest paragraph must
        # be to the image's top edge to count as "layout-anchored"
        self.vertical_window = vertical_window

    def bind(self, doc: ParsedDocument) -> list[Binding]:
        bindings: list[Binding] = []
        # Pre-index elements by page for fast lookup
        by_page: dict[int, list[ParsedElement]] = {}
        for e in doc.elements:
            by_page.setdefault(e.page, []).append(e)
        for elements in by_page.values():
            elements.sort(key=lambda x: x.reading_order)

        # Track current section per reading-order index for section-floor fallback
        section_stack: list[str] = []   # element ids of currently-open section headers
        section_for_order: dict[int, str | None] = {}
        for e in doc.elements:
            if e.role.lower() in ("title", "section_header") and e.text:
                # Pop any deeper levels off the stack, then push this header
                while section_stack:
                    # We don't know level for non-headers; rely on level field if set
                    last_id = section_stack[-1]
                    last_e = next((x for x in doc.elements if x.elem_id == last_id), None)
                    if last_e and last_e.level >= max(e.level, 1):
                        section_stack.pop()
                    else:
                        break
                section_stack.append(e.elem_id)
            section_for_order[e.reading_order] = (
                section_stack[-1] if section_stack else None
            )

        for img in doc.images:
            b = self._bind_one(img, by_page.get(img.page, []), doc.elements,
                               section_for_order)
            if b is not None:
                bindings.append(b)

        # Counters for visibility
        counts = {m.value: 0 for m in BindingMethod}
        for b in bindings:
            counts[b.method.value] += 1
        log.info("binder.done", doc_id=doc.doc_id, total=len(bindings), **counts)
        return bindings

    def _bind_one(
        self,
        img: ParsedImage,
        page_elements: list[ParsedElement],
        all_elements: list[ParsedElement],
        section_for_order: dict[int, str | None],
    ) -> Binding | None:
        # 1. Explicit reference within ±5 elements in reading order
        explicit = self._explicit_reference(img, page_elements)
        if explicit is not None:
            return Binding(elem_id=explicit.elem_id, image_id=img.image_id,
                           method=BindingMethod.EXPLICIT_REFERENCE, score=1.0)

        # 2. Caption directly below
        caption = self._caption_below(img, page_elements)
        if caption is not None:
            return Binding(elem_id=caption.elem_id, image_id=img.image_id,
                           method=BindingMethod.CAPTIONED, score=0.9)

        # 3. Layout-anchored: nearest preceding paragraph on the same page
        anchor = self._layout_anchor(img, page_elements)
        if anchor is not None:
            return Binding(elem_id=anchor.elem_id, image_id=img.image_id,
                           method=BindingMethod.LAYOUT_ANCHORED, score=0.7)

        # 4. Section floor: enclosing heading
        section_id = section_for_order.get(img.reading_order)
        if section_id is not None:
            return Binding(elem_id=section_id, image_id=img.image_id,
                           method=BindingMethod.SECTION_FLOOR, score=0.5)

        # Last resort: bind to nearest element by reading order on any page
        if all_elements:
            nearest = min(all_elements,
                          key=lambda e: abs(e.reading_order - img.reading_order))
            return Binding(elem_id=nearest.elem_id, image_id=img.image_id,
                           method=BindingMethod.UNBOUND, score=0.2)
        return None

    @staticmethod
    def _explicit_reference(img: ParsedImage,
                            page_elements: list[ParsedElement]) -> ParsedElement | None:
        """Look ±5 reading-order positions for explicit "Figure X" / deictic phrases."""
        candidates = [e for e in page_elements
                      if abs(e.reading_order - img.reading_order) <= 5
                      and e.role.lower() in ("paragraph", "text", "list_item",
                                             "section_header", "title")]
        for e in candidates:
            if _REF_PATTERN.search(e.text) or _DEICTIC_PATTERN.search(e.text):
                return e
        return None

    @staticmethod
    def _caption_below(img: ParsedImage,
                       page_elements: list[ParsedElement]) -> ParsedElement | None:
        """A Caption element directly after the image in reading order, on same page."""
        x0_i, y0_i, x1_i, y1_i = img.bbox
        for e in page_elements:
            if e.reading_order <= img.reading_order:
                continue
            if e.role.lower() == "caption":
                # Sanity check: caption should be roughly horizontally aligned
                ex0, ey0, ex1, ey1 = e.bbox
                if ex0 < x1_i and ex1 > x0_i:
                    return e
                # Even if alignment fails, captions are reliable signals
                return e
            # Stop scanning after one non-caption element past the image
            if e.reading_order > img.reading_order + 2:
                break
        return None

    def _layout_anchor(self, img: ParsedImage,
                       page_elements: list[ParsedElement]) -> ParsedElement | None:
        """Nearest preceding paragraph on the same page within vertical_window."""
        x0_i, y0_i, x1_i, y1_i = img.bbox
        best: ParsedElement | None = None
        best_dy = float("inf")
        for e in page_elements:
            if e.reading_order >= img.reading_order:
                continue
            if e.role.lower() not in ("paragraph", "text", "list_item"):
                continue
            ex0, ey0, ex1, ey1 = e.bbox
            # PDF coords have y increasing downward in our normalized form.
            # We want the element whose bottom edge is closest above the image's top.
            dy = abs(y0_i - ey1)
            if dy < best_dy and dy <= self.vertical_window:
                best = e
                best_dy = dy
        return best
