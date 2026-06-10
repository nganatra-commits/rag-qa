"""DynamoDB-backed persistence for per-turn SME feedback.

Schema (PK = request_id, upsert-style — clicking 👍 then 👎 replaces, not stacks):

    {
        "request_id":     "<uuid>",            # partition key
        "rating":         1 | -1 | 0,           # 1 up, -1 down, 0 neutral
        "note":           "...",                # optional SME comment
        "query":          "...",                # echoed user question
        "answer_excerpt": "first ~200 chars of the assistant answer",
        "build_sha":      "<git_sha>",          # which build produced the answer
        "intent":         "procedural|...",     # from /answer (may be None)
        "answerable":     <float | None>,
        "faithfulness":   <float | None>,
        "low_confidence": <bool>,
        "created_at":     <epoch-ms int>,
    }

A blank `feedback_table` setting disables persistence — the route falls back
to log-only (same as the pre-Ring-3 stub) so dev environments work without
DynamoDB.
"""
from __future__ import annotations

import time
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ragqa.config import Settings, get_settings
from ragqa.core.logging import get_logger

log = get_logger(__name__)


class FeedbackStoreUnavailable(RuntimeError):
    """Raised when persistence is not configured (blank table name)."""


class FeedbackStore:
    def __init__(self, table_name: str, region: str) -> None:
        if not table_name:
            raise FeedbackStoreUnavailable("RAGQA_FEEDBACK_TABLE is not set")
        self.table_name = table_name
        self.region = region
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def put(self, record: dict[str, Any]) -> None:
        """Upsert a feedback record keyed by request_id.

        Floats are converted to Decimal because DynamoDB rejects native
        Python floats. None values are dropped (DynamoDB tolerates missing
        attributes; serializing NULL adds noise).
        """
        now = int(time.time() * 1000)
        item: dict[str, Any] = {
            "request_id":     record["request_id"],
            "rating":         int(record.get("rating", 0)),
            "note":           (record.get("note") or "")[:2000],
            "query":          (record.get("query") or "")[:1000],
            "answer_excerpt": (record.get("answer_excerpt") or "")[:500],
            "build_sha":      record.get("build_sha") or "",
            "intent":         record.get("intent") or "",
            "low_confidence": bool(record.get("low_confidence", False)),
            "created_at":     now,
        }
        for k in ("answerable", "faithfulness"):
            v = record.get(k)
            if v is not None:
                item[k] = Decimal(str(round(float(v), 4)))
        try:
            self._table.put_item(Item=item)
        except ClientError as e:
            log.error("feedback.put.fail", request_id=item["request_id"],
                      err=str(e))
            raise


@lru_cache(maxsize=1)
def get_feedback_store() -> FeedbackStore:
    s: Settings = get_settings()
    return FeedbackStore(table_name=s.feedback_table, region=s.feedback_region)
