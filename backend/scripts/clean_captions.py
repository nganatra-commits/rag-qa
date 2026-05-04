"""One-off: walk the caption cache and repair entries where the model's
response was truncated and our fallback stored the raw JSON in alt_text.

Idempotent. Re-runnable.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ragqa.config import get_settings  # noqa: E402

# A caption is "junk" when alt_text starts with `{` or `"alt_text":` or
# similarly looks like raw JSON.
JSON_LEAK_RE = re.compile(r'^\s*[{\[]|^\s*"(alt_text|ocr_text|caption)"\s*:')


def looks_like_json_leak(s: str) -> bool:
    return bool(JSON_LEAK_RE.match(s))


def try_repair(raw: str) -> dict[str, str]:
    """Best-effort extract alt_text/ocr_text/caption from truncated JSON."""
    out = {"alt_text": "", "ocr_text": "", "caption": ""}
    for field in out.keys():
        # Match `"field": "..."` allowing escaped quotes inside
        m = re.search(rf'"{field}"\s*:\s*"((?:\\.|[^"\\])*)"', raw, re.DOTALL)
        if m:
            try:
                # Re-decode JSON escapes (\n, \t, \\, etc.)
                out[field] = json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                out[field] = m.group(1)
    return out


def main() -> int:
    s = get_settings()
    cache_dir = s.cache_dir / "captions"
    if not cache_dir.exists():
        print(f"no caption cache at {cache_dir}")
        return 0

    files = sorted(cache_dir.glob("*.json"))
    print(f"scanning {len(files)} cached captions in {cache_dir}")

    repaired = cleared = ok = 0
    for fp in files:
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        cap = obj.get("caption", {})
        alt = cap.get("alt_text", "") or ""
        if not looks_like_json_leak(alt):
            ok += 1
            continue
        # Try to repair
        fixed = try_repair(alt)
        if fixed["alt_text"] and not looks_like_json_leak(fixed["alt_text"]):
            obj["caption"] = fixed
            fp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            repaired += 1
        else:
            # Couldn't repair - clear the junk so the UI shows nothing rather
            # than the raw JSON. The image will get re-captioned cleanly on
            # the next ingestion run since cache miss.
            fp.unlink()
            cleared += 1

    print(f"  ok      : {ok}")
    print(f"  repaired: {repaired}")
    print(f"  cleared : {cleared}  (will re-caption on next ingest)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
