"""Smoke test for /health. Stubs out the Pinecone store so this runs offline."""
from __future__ import annotations

import os

# Ensure required env vars exist before importing the app
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PINECONE_API_KEY", "test-key")
os.environ.setdefault("RAGQA_DATA_DIR", os.path.join(os.path.dirname(__file__), "_tmp_data"))

from fastapi.testclient import TestClient  # noqa: E402

from ragqa.api import deps  # noqa: E402
from ragqa.main import app  # noqa: E402


class _FakeStore:
    def ensure_index(self) -> None: ...
    def stats(self) -> dict:
        return {"namespaces": {"v1": {"vector_count": 0}}}


def _override_store():
    return _FakeStore()


app.dependency_overrides[deps.get_store] = _override_store


def test_health_ok() -> None:
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "namespace" in body
