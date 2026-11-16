"""Exact answer cache for POST /ask, in Postgres (PLAN, Etapa 7).

The key is a sha256 over the normalised question, the answer language, the index
version, the configured LLM model, the prompt version, the retrieval filters (works,
passage language, include_dubious) and the settings that change an answer (k, context
and output budgets, abstention threshold, reasoning effort). Changing any of them is a
miss, so a new index, prompt or model never serves an old answer.

Only complete answers from the configured model are stored. Search-only results and
errors are transient; answers from the fallback provider come from another model; a
low-similarity abstention did not call the LLM. Entries expire after
settings.answer_cache_ttl_days (0 or less switches the cache off). The question is
stored only as a hash.

The cache never fails a request: a database error is logged and treated as a miss.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.models import AskResult
from app.settings import Settings

log = logging.getLogger(__name__)

_SPACE_RE = re.compile(r"\s+")
_EDGE_PUNCT = "¿¡?!.…,;: \"'«»“”‘’"


def normalise_question(text: str) -> str:
    """Unicode NFKC, single spaces, without opening or closing punctuation:
    "  ¿Qué es la  virtud? " -> "Qué es la virtud". Accents and case are kept: retrieval
    reads case (a capitalised work title is a soft work filter, "EN II.6" a reference), so
    questions that differ only in case may get different passages."""
    text = unicodedata.normalize("NFKC", text)
    return _SPACE_RE.sub(" ", text).strip().strip(_EDGE_PUNCT).strip()


def question_sha256(text: str) -> str:
    return hashlib.sha256(normalise_question(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheKey:
    key: str
    question_sha256: str
    answer_lang: str
    index_version: str
    model: str
    prompt_version: str
    filters: dict[str, Any] = field(default_factory=dict)


def make_key(
    question: str,
    *,
    answer_lang: str,
    index_version: str,
    model: str,
    prompt_version: str,
    works: list[str] | None,
    lang: str | None,
    include_dubious: bool,
    settings: Settings,
) -> CacheKey:
    filters = {
        "works": sorted(works) if works else None,
        "lang": lang,
        "include_dubious": include_dubious,
    }
    generation = {
        "retrieval_k": settings.retrieval_k,
        "context_max_tokens": settings.context_max_tokens,
        "output_max_tokens": settings.output_max_tokens,
        "abstain_min_similarity": settings.abstain_min_similarity,
        "llm_reasoning_effort": settings.llm_reasoning_effort,
    }
    q_hash = question_sha256(question)
    parts = {
        "question": q_hash,
        "answer_lang": answer_lang,
        "index_version": index_version,
        "model": model,
        "prompt_version": prompt_version,
        "filters": filters,
        "generation": generation,
    }
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()
    return CacheKey(
        key=digest,
        question_sha256=q_hash,
        answer_lang=answer_lang,
        index_version=index_version,
        model=model,
        prompt_version=prompt_version,
        filters=filters,
    )


def cacheable(result: AskResult, model: str) -> bool:
    """Whether an answer may be stored under a key built for `model` (see module)."""
    return bool(model) and not result.search_only and result.error is None and result.model == model


class AnswerCache:
    """The answer_cache table. Each method runs in its own transaction (a savepoint inside
    a caller's transaction), so a failure leaves the connection usable."""

    def __init__(self, conn: psycopg.Connection, ttl_days: int = 30) -> None:
        self.conn = conn
        self.ttl_days = ttl_days

    def get(self, key: CacheKey) -> dict | None:
        """The stored response, or None on a miss, an expired entry or a database error.
        A hit increments the entry's hit counter."""
        if self.ttl_days <= 0:
            return None
        try:
            with self.conn.transaction():
                row = self.conn.execute(
                    """
                    UPDATE answer_cache SET hits = hits + 1, last_hit_at = now()
                    WHERE cache_key = %s AND created_at > now() - make_interval(days => %s)
                    RETURNING response
                    """,
                    (key.key, self.ttl_days),
                ).fetchone()
        except psycopg.Error as exc:
            log.warning("Caché de respuestas no disponible (lectura): %s", exc)
            return None
        return row[0] if row else None

    def put(self, key: CacheKey, response: dict) -> bool:
        """Store (or replace) a response and delete expired entries. False on a database
        error, which is logged."""
        if self.ttl_days <= 0:
            return False
        try:
            with self.conn.transaction():
                self.conn.execute(
                    """
                    INSERT INTO answer_cache (cache_key, question_sha256, answer_lang,
                        index_version, model, prompt_version, filters, response)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cache_key) DO UPDATE SET response = EXCLUDED.response,
                        created_at = now(), hits = 0, last_hit_at = NULL
                    """,
                    (
                        key.key,
                        key.question_sha256,
                        key.answer_lang,
                        key.index_version,
                        key.model,
                        key.prompt_version,
                        Jsonb(key.filters),
                        Jsonb(response),
                    ),
                )
                self.conn.execute(
                    "DELETE FROM answer_cache WHERE created_at < now() - make_interval(days => %s)",
                    (self.ttl_days,),
                )
        except psycopg.Error as exc:
            log.warning("Caché de respuestas no disponible (escritura): %s", exc)
            return False
        return True
