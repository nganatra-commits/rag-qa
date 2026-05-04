"""Minimal direct-HTTP OpenAI client.

Why not the official `openai` SDK: it hangs on this Windows setup (some init
issue). curl, httpx, and requests all work fine against the same endpoint
with the same key, so we use httpx directly and skip the SDK entirely.

Implements only what the ingestion + serving pipelines need:
  - POST /v1/embeddings
  - POST /v1/chat/completions  (with vision content blocks)
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class OpenAIError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"OpenAI HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


class OpenAIRateLimitError(OpenAIError):
    pass


class OpenAIClient:
    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 4,
    ):
        self._base = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout
        self._max_retries = max_retries

    # --- Public methods ---

    def embeddings(self, model: str, inputs: list[str]) -> list[list[float]]:
        body = {"model": model, "input": inputs}
        data = self._post_json("/embeddings", body)
        # Sort by index to be safe (the API does it, but defensive)
        items = sorted(data.get("data", []), key=lambda r: r.get("index", 0))
        return [item["embedding"] for item in items]

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        # gpt-5 / o1 / o3 family renamed max_tokens -> max_completion_tokens
        # AND fix temperature at 1.0 (no override). Only send custom temperature
        # for older families (gpt-4o, gpt-4-turbo, etc.).
        is_new_family = model.startswith(("gpt-5", "o1", "o3", "o4"))
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if is_new_family:
            body["max_completion_tokens"] = max_tokens
            # temperature is fixed at 1.0 for these models; do not send it
        else:
            body["max_tokens"] = max_tokens
            body["temperature"] = temperature
        if response_format is not None:
            body["response_format"] = response_format
        return self._post_json("/chat/completions", body, timeout=timeout)

    # --- Internals ---

    @retry(
        retry=retry_if_exception_type((OpenAIRateLimitError,
                                       httpx.ConnectError,
                                       httpx.ReadTimeout,
                                       httpx.RemoteProtocolError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    def _post_json(
        self,
        path: str,
        body: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        with httpx.Client(timeout=timeout or self._timeout) as client:
            r = client.post(url, headers=self._headers, json=body)
        if r.status_code == 429:
            raise OpenAIRateLimitError(r.status_code, r.text)
        if r.status_code >= 400:
            raise OpenAIError(r.status_code, r.text)
        return r.json()
