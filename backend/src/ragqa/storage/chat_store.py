"""DynamoDB-backed persistence for chat sessions.

Schema (PK only — no GSIs needed at this scale):

    {
        "id":         "<uuid>",            # partition key
        "title":      "...",
        "updated_at": <epoch-ms int>,
        "created_at": <epoch-ms int>,
        "doc_filter": ["qaman", ...],      # str[]
        "turns":      "<json string>",     # serialized to keep DynamoDB
                                           # types simple (turns can be deeply
                                           # nested AnswerResponse blobs).
    }

A blank `chats_table` setting disables persistence — endpoints will
return 503 so the frontend can fall back to localStorage.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ragqa.config import Settings, get_settings
from ragqa.core.logging import get_logger

log = get_logger(__name__)


class ChatStoreUnavailable(RuntimeError):
    """Raised when persistence is not configured (blank table name)."""


class ChatStore:
    def __init__(self, table_name: str, region: str, max_list: int) -> None:
        if not table_name:
            raise ChatStoreUnavailable("RAGQA_CHATS_TABLE is not set")
        self.table_name = table_name
        self.region = region
        self.max_list = max_list
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        try:
            res = self._table.get_item(Key={"id": chat_id})
        except ClientError as e:
            log.error("chats.get.fail", chat_id=chat_id, err=str(e))
            raise
        item = res.get("Item")
        if not item:
            return None
        return _decode(item)

    def put(self, chat: dict[str, Any]) -> dict[str, Any]:
        """Upsert a chat. Caller-provided `id`, `title`, `turns`, `doc_filter`."""
        now = int(time.time() * 1000)
        record = {
            "id":         chat["id"],
            "title":      (chat.get("title") or "New chat")[:200],
            "updated_at": now,
            "created_at": int(chat.get("created_at") or now),
            "doc_filter": list(chat.get("doc_filter") or []),
            # Serialize turns as a JSON string. DynamoDB caps an item at 400 KB;
            # a JSON string is fine for hundreds of turns of typical Q&A.
            "turns":      json.dumps(chat.get("turns") or []),
        }
        self._table.put_item(Item=record)
        return _decode(record)

    def delete(self, chat_id: str) -> None:
        self._table.delete_item(Key={"id": chat_id})

    def list_recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return the most-recently-updated chats.

        DynamoDB scan is fine at this scale (we expect <1000 chats); avoiding
        the overhead of a GSI keeps the table cheap. We sort client-side.
        Each row in the returned list has `turns` STRIPPED — listings only
        carry id/title/updated_at/created_at/doc_filter for the sidebar.
        """
        cap = limit or self.max_list
        items: list[dict[str, Any]] = []
        # ProjectionExpression skips `turns` (the heavy column) over the wire.
        kwargs = {
            "ProjectionExpression": "#i, title, updated_at, created_at, doc_filter",
            "ExpressionAttributeNames": {"#i": "id"},
        }
        last_eval: dict[str, Any] | None = None
        while True:
            if last_eval:
                kwargs["ExclusiveStartKey"] = last_eval
            res = self._table.scan(**kwargs)
            items.extend(res.get("Items", []))
            last_eval = res.get("LastEvaluatedKey")
            if not last_eval:
                break
            if len(items) >= cap * 4:  # sanity cap; plenty for client-side sort
                break
        items.sort(key=lambda r: int(r.get("updated_at", 0)), reverse=True)
        out = items[:cap]
        for r in out:
            r["doc_filter"] = list(r.get("doc_filter") or [])
            r["updated_at"] = int(r.get("updated_at", 0))
            r["created_at"] = int(r.get("created_at", 0))
        return out


def _decode(item: dict[str, Any]) -> dict[str, Any]:
    """Decode a DynamoDB item to the wire format the frontend consumes.

    Turns are stored as a JSON string for schema simplicity; expand them on
    read so the API contract stays plain JSON.
    """
    turns_raw = item.get("turns")
    if isinstance(turns_raw, str):
        try:
            turns = json.loads(turns_raw)
        except json.JSONDecodeError:
            turns = []
    elif isinstance(turns_raw, list):
        turns = turns_raw
    else:
        turns = []
    return {
        "id":         item["id"],
        "title":      item.get("title") or "New chat",
        "updated_at": int(item.get("updated_at", 0)),
        "created_at": int(item.get("created_at", 0)),
        "doc_filter": list(item.get("doc_filter") or []),
        "turns":      turns,
    }


@lru_cache(maxsize=1)
def get_chat_store() -> ChatStore:
    s: Settings = get_settings()
    return ChatStore(
        table_name=s.chats_table,
        region=s.chats_region,
        max_list=s.chats_max_list,
    )
