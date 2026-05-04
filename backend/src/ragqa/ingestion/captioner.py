"""VLM captioner: turn each image into (alt_text, ocr_text, caption).

Uses OpenAI Chat Completions with vision (gpt-4o by default). Output is
JSON-mode so we get a strict, parseable response. Per-image cost is
dominated by the image tokens (~700) and ~150 output tokens.

Cache: keyed by sha256(image bytes). Re-runs are free.
"""
from __future__ import annotations

import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ragqa.core.errors import GenerationError
from ragqa.core.logging import get_logger
from ragqa.core.openai_http import OpenAIClient, OpenAIError

log = get_logger(__name__)


SYSTEM_PROMPT = """\
You describe images extracted from a software user manual. The product is
NWA Quality Analyst, a desktop SPC / quality-control application. Most images
are screenshots of dialogs, menus, charts, and configuration windows.

For each image, return STRICT JSON with three fields:

  "alt_text"  : one concise sentence (<=20 words) describing what the image *is*.
                **Do not include version numbers (like "v7", "Quality Analyst 7",
                "7.0.93.0") in alt_text or caption — many screenshots come from
                an older revision of the product and the version visible in the
                title bar is misleading.** Refer to the product as "NWA Quality
                Analyst" generically. Example: "Setup Wizard welcome screen
                with Next and Cancel buttons."
  "ocr_text"  : the verbatim visible text inside the image (menu labels, button
                text, field values, header text, version strings if shown).
                Tab-separate columns within a row, newline-separate rows. This
                field IS the literal contents — keep version numbers here.
                Empty string if no readable text.
  "caption"   : a one-sentence semantic summary suitable for inline display
                under the image, written for a user trying to follow the manual.
                Same version-number rule as alt_text — omit them.
                Example: "The Setup Wizard welcome screen; click Next to begin."

Return ONLY a JSON object with those three keys. No prose, no markdown fences.
"""


class ImageCaption(BaseModel):
    alt_text: str = ""
    ocr_text: str = ""
    caption: str = ""


class _CachedCaption(BaseModel):
    image_sha256: str
    model: str
    caption: ImageCaption


class VLMCaptioner:
    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        model: str = "gpt-4o",
        base_url: str | None = None,
        max_workers: int = 6,
        max_output_tokens: int = 400,
    ):
        self._client = OpenAIClient(api_key=api_key, base_url=base_url, timeout=120.0)
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = model
        self._max_workers = max_workers
        self._max_tokens = max_output_tokens

    def caption_many(self, image_paths: list[Path]) -> dict[Path, ImageCaption]:
        """Caption a batch of images concurrently. Returns {path: caption}."""
        results: dict[Path, ImageCaption] = {}
        if not image_paths:
            return results

        log.info("captioner.batch.start",
                 count=len(image_paths), workers=self._max_workers, model=self._model)

        with ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            futures = {ex.submit(self._caption_one, p): p for p in image_paths}
            done = 0
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    results[p] = fut.result()
                except Exception as e:
                    log.warning("captioner.image.fail", image=str(p), err=repr(e))
                    results[p] = ImageCaption()
                done += 1
                if done % 50 == 0:
                    log.info("captioner.progress", done=done, total=len(image_paths))

        log.info("captioner.batch.done", count=len(results))
        return results

    def _caption_one(self, image_path: Path) -> ImageCaption:
        cached = self._cache_get(image_path)
        if cached is not None:
            return cached
        cap = self._call_openai(image_path)
        self._cache_put(image_path, cap)
        return cap

    def _call_openai(self, image_path: Path) -> ImageCaption:
        image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
        media_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        data_url = f"data:{media_type};base64,{image_b64}"

        try:
            resp = self._client.chat_completion(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url",
                             "image_url": {"url": data_url, "detail": "high"}},
                            {"type": "text",
                             "text": "Return the JSON described in the system prompt."},
                        ],
                    },
                ],
                max_tokens=self._max_tokens,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
        except OpenAIError:
            raise
        except Exception as e:
            raise GenerationError(f"caption call failed for {image_path.name}: {e}") from e

        text = ""
        choices = resp.get("choices") or []
        if choices:
            text = (choices[0].get("message", {}).get("content") or "").strip()
        try:
            data = json.loads(text)
            return ImageCaption(**data)
        except (json.JSONDecodeError, ValidationError) as e:
            log.warning("captioner.parse.fail", err=repr(e), raw=text[:200])
            return ImageCaption(alt_text=text[:200])

    # --- Cache helpers ---

    def _cache_path(self, image_path: Path) -> Path:
        h = hashlib.sha256(image_path.read_bytes()).hexdigest()
        return self._cache_dir / "captions" / f"{h}.json"

    def _cache_get(self, image_path: Path) -> ImageCaption | None:
        cp = self._cache_path(image_path)
        if not cp.exists():
            return None
        try:
            obj = _CachedCaption.model_validate_json(cp.read_text(encoding="utf-8"))
            if obj.model != self._model:
                return None  # cached under a different model; re-caption
            return obj.caption
        except Exception:
            return None

    def _cache_put(self, image_path: Path, cap: ImageCaption) -> None:
        cp = self._cache_path(image_path)
        cp.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256(image_path.read_bytes()).hexdigest()
        obj = _CachedCaption(image_sha256=h, model=self._model, caption=cap)
        cp.write_text(obj.model_dump_json(), encoding="utf-8")
