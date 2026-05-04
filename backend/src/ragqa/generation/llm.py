"""Multimodal answer generator (OpenAI Chat Completions).

Sends the user query + retrieved chunk text + bound image bytes (up to N) as
a single Chat Completions request with vision. The model can both *read*
the chunk text and *see* the screenshots when forming its answer.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from ragqa.core.errors import GenerationError
from ragqa.core.logging import get_logger
from ragqa.core.openai_http import OpenAIClient, OpenAIError
from ragqa.generation.prompts import (
    ANSWER_SYSTEM_PROMPT,
    build_user_message,
    format_chunks_block,
)
from ragqa.models.chunks import RetrievalHit

log = get_logger(__name__)


@dataclass
class AnswerResult:
    answer: str
    cited_chunk_ids: list[str]
    cited_image_ids: list[str]
    used_chunks: list[RetrievalHit]
    used_images: list[str]
    input_tokens: int
    output_tokens: int


class MultimodalAnswerer:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str | None = None,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
        max_images: int = 4,
    ):
        self._client = OpenAIClient(api_key=api_key, base_url=base_url, timeout=120.0)
        self._model = model
        self._max_tokens = max_output_tokens
        self._temperature = temperature
        self._max_images = max_images

    def answer(
        self,
        query: str,
        hits: list[RetrievalHit],
    ) -> AnswerResult:
        if not hits:
            return AnswerResult(
                answer="I could not find anything in the manuals matching your question.",
                cited_chunk_ids=[], cited_image_ids=[], used_chunks=[],
                used_images=[], input_tokens=0, output_tokens=0,
            )

        chunks_block = format_chunks_block(hits)
        user_text = build_user_message(query, chunks_block)
        image_blocks, used_images = self._image_blocks(hits)

        log.info("answer.call",
                 model=self._model, hits=len(hits),
                 images_attached=len(image_blocks),
                 prompt_chars=len(user_text))

        try:
            resp = self._client.chat_completion(
                model=self._model,
                messages=[
                    {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            *image_blocks,
                            {"type": "text", "text": user_text},
                        ],
                    },
                ],
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
        except OpenAIError as e:
            raise GenerationError(f"answer call failed: {e}") from e
        except Exception as e:
            raise GenerationError(f"answer call failed: {e}") from e

        text = ""
        choices = resp.get("choices") or []
        if choices:
            text = (choices[0].get("message", {}).get("content") or "").strip()
        usage = resp.get("usage") or {}
        cited_chunks = [h.chunk.chunk_id for h in hits]
        cited_images = self._extract_image_ids(text)

        return AnswerResult(
            answer=text,
            cited_chunk_ids=cited_chunks,
            cited_image_ids=cited_images,
            used_chunks=hits,
            used_images=used_images,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )

    def _image_blocks(self, hits: list[RetrievalHit]) -> tuple[list[dict], list[str]]:
        """Build OpenAI image_url content blocks for the top images across the
        retrieved chunks, deduped by image_id, capped at self._max_images.
        """
        seen: set[str] = set()
        blocks: list[dict] = []
        used: list[str] = []
        for h in hits:
            for img in h.chunk.images:
                if len(blocks) >= self._max_images:
                    break
                if img.image_id in seen:
                    continue
                seen.add(img.image_id)
                p = Path(img.uri)
                if not p.exists():
                    log.warning("answer.image.missing", path=str(p))
                    continue
                try:
                    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
                except Exception as e:
                    log.warning("answer.image.read_fail", err=repr(e), path=str(p))
                    continue
                media_type = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                blocks.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{data}",
                        "detail": "high",
                    },
                })
                used.append(img.image_id)
        return blocks, used

    @staticmethod
    def _extract_image_ids(text: str) -> list[str]:
        """Pull [FIGURE: <id>] references the model produced from its answer."""
        import re
        return re.findall(r"\[FIGURE:\s*([A-Za-z0-9_\-]+)\s*\]", text)
