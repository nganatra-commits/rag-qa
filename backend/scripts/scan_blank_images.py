"""Scan extracted images and flag those that are mostly-uniform (black, white,
or single-color blocks) so the API can filter them out of /answer responses.

Writes data/cache/blank_image_ids.txt - one image_id per line.
Idempotent. Run after ingestion (or any time).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ragqa.config import get_settings  # noqa: E402


# Heuristics
THUMB_SIZE = 64        # downscale before stats - way faster
STD_THRESHOLD = 8.0    # if pixel std-dev across all channels is below this, blank


def is_blank(p: Path) -> tuple[bool, float]:
    try:
        with Image.open(p) as img:
            img = img.convert("RGB")
            img.thumbnail((THUMB_SIZE, THUMB_SIZE))
            arr = np.asarray(img, dtype=np.float32)
    except Exception as e:
        print(f"  ! cannot open {p.name}: {e!r}")
        return False, -1.0
    std = float(arr.std())
    return std < STD_THRESHOLD, std


def main() -> int:
    s = get_settings()
    images_dir = s.images_dir
    out_file = s.cache_dir / "blank_image_ids.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if not images_dir.exists():
        print(f"no images dir at {images_dir}")
        return 0

    blanks: list[str] = []
    total = 0
    for png in images_dir.rglob("*.png"):
        total += 1
        blank, std = is_blank(png)
        if blank:
            image_id = png.stem
            blanks.append(image_id)
            if len(blanks) <= 8:
                print(f"  blank: {image_id}  (std={std:.2f})")

    out_file.write_text("\n".join(sorted(blanks)) + ("\n" if blanks else ""),
                        encoding="utf-8")
    pct = (len(blanks) / total * 100) if total else 0
    print(f"\nscanned {total} images, found {len(blanks)} blank ({pct:.1f}%)")
    print(f"wrote {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
