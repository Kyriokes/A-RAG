"""JSON logs for the API, and one log line per query (PLAN, Etapa 7).

setup_logging() makes the root logger write one JSON object per line on stdout, which is
what the hosting keeps. log_query() writes the per-query line on the "arag.query" logger:
request id, endpoint, status, detected language, filters, retrieved chunk ids and scores,
index and prompt versions, model, tokens, latency per stage, abstention, search-only
mode, error and cache use. The question goes as a hash of its normalised form (the same
hash the answer cache stores); settings.log_question_text=true adds the text, for local
debugging only.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from app.cache import question_sha256
from app.settings import Settings

QUERY_LOGGER = "arag.query"
_HANDLER_NAME = "arag-json"


class JsonFormatter(logging.Formatter):
    """One JSON object per record. A record logged with extra={"event": {...}} becomes
    that object (plus timestamp, level and logger); any other record keeps its message."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
        }
        event = getattr(record, "event", None)
        if isinstance(event, dict):
            out.update(event)
        else:
            out["msg"] = record.getMessage()
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", stream: TextIO | None = None) -> logging.Handler:
    """Add the JSON handler to the root logger once (later calls only set the level)."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in root.handlers:
        if handler.get_name() == _HANDLER_NAME:
            return handler
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    return handler


def question_fields(question: str, settings: Settings) -> dict[str, Any]:
    """How a question appears in the query log: a hash and its length, never the text
    unless settings.log_question_text is on."""
    fields: dict[str, Any] = {
        "question_sha256": question_sha256(question)[:16],
        "question_chars": len(question),
    }
    if settings.log_question_text:
        fields["question"] = question
    return fields


def retrieved_fields(retrieved: list) -> list[dict[str, Any]]:
    """Chunk id, rank, score and method of each hit (RetrievedChunk or the API's ref)."""
    out = []
    for hit in retrieved:
        chunk_id = hit.chunk.chunk_id if hasattr(hit, "chunk") else hit.chunk_id
        out.append(
            {
                "chunk_id": chunk_id,
                "rank": hit.rank,
                "score": round(float(hit.score), 4),
                "method": hit.method,
            }
        )
    return out


def log_query(event: dict[str, Any]) -> None:
    """Write the per-query line."""
    logging.getLogger(QUERY_LOGGER).info("query", extra={"event": {"event": "query", **event}})
