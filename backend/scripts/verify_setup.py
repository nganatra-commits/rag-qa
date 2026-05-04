"""Pre-flight check: imports + Pinecone + OpenAI reachability.

Run BEFORE the costly ingestion to catch any setup issues for free.
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

# Make src/ importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def heading(s: str) -> None:
    print(f"\n=== {s} ===")


def main() -> int:
    fail = 0

    heading("1. ragqa module imports")
    modules = [
        "ragqa.config",
        "ragqa.core.logging",
        "ragqa.core.errors",
        "ragqa.models.chunks",
        "ragqa.api.schemas",
        "ragqa.ingestion.parser",
        "ragqa.ingestion.binder",
        "ragqa.ingestion.captioner",
        "ragqa.ingestion.chunker",
        "ragqa.ingestion.pipeline",
        "ragqa.retrieval.embeddings",
        "ragqa.retrieval.vectorstore",
        "ragqa.retrieval.hybrid",
        "ragqa.retrieval.rerank",
        "ragqa.generation.llm",
        "ragqa.generation.prompts",
        "ragqa.api.routes",
        "ragqa.main",
    ]
    for m in modules:
        try:
            importlib.import_module(m)
            print(f"  ok  {m}")
        except Exception as e:
            print(f"  FAIL {m}: {type(e).__name__}: {e}")
            fail += 1
    if fail:
        return 1

    heading("2. settings load (.env)")
    from ragqa.config import get_settings
    s = get_settings()
    s.ensure_dirs()
    print(f"  vlm_model         : {s.vlm_model}")
    print(f"  llm_model         : {s.llm_model}")
    print(f"  embedding_model   : {s.embedding_model}  (dim={s.embedding_dim})")
    print(f"  pinecone_index    : {s.pinecone_index}")
    print(f"  pinecone_namespace: {s.pinecone_namespace}")
    print(f"  pinecone_region   : {s.pinecone_cloud}/{s.pinecone_region}")
    print(f"  data_dir          : {s.data_dir.resolve()}")
    print(f"  openai_key set    : {bool(s.openai_api_key)} ({s.openai_api_key[:10]}...)")
    print(f"  pinecone_key set  : {bool(s.pinecone_api_key)} ({s.pinecone_api_key[:10]}...)")

    heading("3. Pinecone connectivity")
    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=s.pinecone_api_key)
        t0 = time.time()
        idxs = pc.list_indexes()
        dt = (time.time() - t0) * 1000
        names = [i.name for i in idxs] if hasattr(idxs, "__iter__") else \
                [i["name"] for i in idxs.get("indexes", [])]
        print(f"  list_indexes: {len(names)} index(es) in account ({dt:.0f} ms)")
        for n in names:
            marker = "  <-- target" if n == s.pinecone_index else ""
            print(f"    - {n}{marker}")
        if s.pinecone_index not in names:
            print(f"  note: target index '{s.pinecone_index}' will be auto-created on first ingest")
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        fail += 1

    heading("4. OpenAI connectivity (cheap models.list call)")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url or None)
        t0 = time.time()
        models = client.models.list()
        dt = (time.time() - t0) * 1000
        ids = [m.id for m in models.data]
        print(f"  models.list: {len(ids)} models accessible ({dt:.0f} ms)")
        target = s.vlm_model
        if target in ids:
            print(f"  target model '{target}' available")
        else:
            close = [m for m in ids if target.split("-")[0] in m][:5]
            print(f"  WARNING: '{target}' not in your model list. Similar: {close}")
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        fail += 1

    heading("5. source PDFs present")
    pdf_dir = s.source_pdfs_dir
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    print(f"  {pdf_dir.resolve()}")
    if not pdfs:
        print(f"  FAIL: no PDFs found")
        fail += 1
    else:
        for p in pdfs:
            print(f"    {p.name:30s} {p.stat().st_size:>15,} B")

    heading("Summary")
    if fail == 0:
        print("  ALL CHECKS PASSED - ready to run ingest_pdfs.py")
        return 0
    print(f"  {fail} check(s) failed - fix before running ingest")
    return 1


if __name__ == "__main__":
    sys.exit(main())
