"""Torch-free dataclasses for the parsed-document shape.

Lives in its own module so PyMuPDFParser can use them without dragging
in Docling/torch transitively.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedElement:
    """One text-bearing element (paragraph, heading, list item, table)."""
    elem_id: str
    role: str               # Title / SectionHeader / Paragraph / ListItem / Table / Caption
    text: str
    page: int
    bbox: tuple[float, float, float, float]
    reading_order: int
    level: int = 0


@dataclass
class ParsedImage:
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
