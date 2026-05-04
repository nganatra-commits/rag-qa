"""One-shot ingestion CLI.

Usage:
    python scripts/ingest_pdfs.py                  # ingest defaults
    python scripts/ingest_pdfs.py --wipe-namespace # clean rebuild
    python scripts/ingest_pdfs.py --pdf-dir D:\\some\\folder
    python scripts/ingest_pdfs.py --doc QAsetup    # only this one
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make src/ importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ragqa.config import get_settings
from ragqa.core.logging import configure_logging, get_logger
from ragqa.ingestion.pipeline import IngestionPipeline


# Default doc_id -> filename. Picks the cleaned file from Track-0 if present,
# else falls back to the original Downloads location.
DEFAULT_DOCS: dict[str, list[Path]] = {
    "qasetup": [
        Path(r"C:\rag-qa\backend\data\source-pdfs\QAsetup.cleaned.pdf"),
        Path(r"C:\Users\nilay\rag\pdf-rewrite\out\QAsetup.cleaned.pdf"),
        Path(r"C:\Users\nilay\Downloads\QAsetup.pdf"),
    ],
    "qatutor": [
        Path(r"C:\rag-qa\backend\data\source-pdfs\QATutor.cleaned.pdf"),
        Path(r"C:\Users\nilay\rag\pdf-rewrite\out\QATutor.cleaned.pdf"),
        Path(r"C:\Users\nilay\Downloads\QATutor.pdf"),
    ],
    "qaman": [
        Path(r"C:\rag-qa\backend\data\source-pdfs\QAman.cleaned.pdf"),
        Path(r"C:\Users\nilay\rag\pdf-rewrite\out\QAman.cleaned.pdf"),
        Path(r"C:\Users\nilay\Downloads\QAman.pdf"),
    ],
}


def resolve(candidates: list[Path]) -> Path:
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"none of these PDFs exist: {', '.join(str(c) for c in candidates)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest PDFs into the RAG index.")
    parser.add_argument("--doc", action="append", default=None,
                        choices=list(DEFAULT_DOCS.keys()),
                        help="restrict to specific doc(s); repeat flag for multiple")
    parser.add_argument("--wipe-namespace", action="store_true",
                        help="delete all vectors in the namespace first (clean rebuild)")
    parser.add_argument("--pdf-dir", type=Path, default=None,
                        help="override: ingest every *.pdf from this directory; "
                             "doc_id is the file stem")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log = get_logger("ingest_pdfs")

    if args.pdf_dir:
        if not args.pdf_dir.is_dir():
            print(f"--pdf-dir {args.pdf_dir} is not a directory", file=sys.stderr)
            return 2
        pdfs = [(p.stem.lower(), p) for p in sorted(args.pdf_dir.glob("*.pdf"))]
    else:
        wanted = args.doc or list(DEFAULT_DOCS.keys())
        pdfs = [(doc_id, resolve(DEFAULT_DOCS[doc_id])) for doc_id in wanted]

    log.info("ingest.cli.plan",
             namespace=settings.pinecone_namespace,
             wipe=args.wipe_namespace,
             pdfs=[(d, str(p)) for d, p in pdfs])

    pipeline = IngestionPipeline(settings)
    summary = pipeline.run(pdfs=pdfs, wipe_namespace=args.wipe_namespace)

    print("\n=== ingestion summary ===")
    for k, v in summary.items():
        print(f"  {k:12} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
