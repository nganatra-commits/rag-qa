"""Find caption cache entries that mention 'Quality Analyst 7' (a captioner
hallucination — the product is version 8) and delete them so the next
ingestion run re-captions just those images.

Usage:
    python scripts/fix_version_captions.py --dry-run   # report only
    python scripts/fix_version_captions.py             # actually delete
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ragqa.config import get_settings  # noqa: E402

# Match "Quality Analyst 7", "QA 7", "Version 7", "v7" - all wrong (it's 8)
WRONG_VERSION_RE = re.compile(
    r"\b("
    r"Quality\s+Analyst\s+7|"
    r"QA\s*7\b|"
    r"NWA\s+Quality\s+Analyst\s+7|"
    r"version\s+7|v\s*7\b"
    r")\b",
    re.IGNORECASE,
)


def is_wrong_caption(obj: dict) -> str | None:
    """Return the offending text if the caption mentions version 7, else None."""
    cap = obj.get("caption", {})
    for field in ("alt_text", "caption", "ocr_text"):
        s = cap.get(field, "")
        if not s:
            continue
        m = WRONG_VERSION_RE.search(s)
        if m:
            return f"{field}: ...{s[max(0, m.start()-30):m.end()+30]}..."
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report only, don't delete")
    args = ap.parse_args()

    s = get_settings()
    cache_dir = s.cache_dir / "captions"
    if not cache_dir.exists():
        print(f"no caption cache at {cache_dir}")
        return 0

    files = sorted(cache_dir.glob("*.json"))
    print(f"scanning {len(files)} cached captions...\n")

    affected: list[Path] = []
    for fp in files:
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        hit = is_wrong_caption(obj)
        if hit is None:
            continue
        affected.append(fp)
        if len(affected) <= 12:
            print(f"  bad: {fp.name[:20]}...  {hit[:120]}")

    if not affected:
        print("\nNo version-7 captions found. Nothing to do.")
        return 0

    print(f"\n{len(affected)} captions reference 'Quality Analyst 7' / 'v7'.")

    if args.dry_run:
        print("(dry-run — no deletions)")
        return 0

    for fp in affected:
        fp.unlink()
    print(f"Deleted {len(affected)} cache files.")
    print("\nRun `python scripts/ingest_pdfs.py --wipe-namespace` to re-caption.")
    print("Cost estimate: ~${:.2f}".format(len(affected) * 0.0035))
    return 0


if __name__ == "__main__":
    sys.exit(main())
