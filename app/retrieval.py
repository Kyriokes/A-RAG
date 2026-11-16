"""Retrieval: the one function the API, the evals and the MCP server all import.

retrieve() works in this order:

1. If the question contains Bekker references (app.bekker.find_refs), the first
   settings.retrieval_max_bekker_refs of them are resolved by range overlap on chunk
   metadata, without embeddings (method "bekker_ref", score 1.0), in Bekker order. Each
   reference reads a bounded number of rows, so a range as wide as "100a-1462b" costs no
   more than a single line. If no chunk overlaps them (a reference outside the corpus),
   retrieval falls through to dense search.
2. Otherwise, exact cosine search over the active index (method "dense", score = cosine
   similarity). Explicit `works`, `lang` and `include_dubious=False` are hard filters.
   Works named in the question (app.query_parse) are a soft filter: their chunks come
   first, and if they give fewer than k hits the rest is filled without that filter.
3. Hits are deduplicated by canonical location: chunks of the same work from different
   editions whose Bekker ranges share at least settings.dedup_min_overlap_lines lines count
   once. The location keeps the position of its best hit and is represented by the chunk
   in the question's language when there is one (its own score is kept).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import psycopg
from psycopg.pq import TransactionStatus

from app import bekker
from app.db import connect
from app.embeddings import Embedder, expected_model_name, get_embedder
from app.models import Authenticity, Chunk, RetrievedChunk, Work
from app.query_parse import detect_lang, detect_works
from app.settings import Settings, get_settings

# Over-fetch factor for dense search, so cross-language duplicates can be dropped and still
# leave k results without a second scan in the common case.
_FETCH_FACTOR = 3

_COLUMN_NAMES = [
    "chunk_id", "unit_id", "work_id", "edition_id", "lang", "divisions", "seq",
    "bekker_start", "bekker_end", "bekker_precision", "text", "embed_text",
]  # fmt: skip
_CHUNK_COLUMNS = ", ".join(_COLUMN_NAMES)


class IndexNotReadyError(RuntimeError):
    """No usable index: none built, or it does not match the configuration (rule 4)."""


@dataclass
class IndexCheck:
    """Result of the startup check, for /readyz."""

    ok: bool
    index_version: str = ""
    embedding_model: str = ""
    embedding_provider: str = ""
    embedding_dim: int | None = None
    chunk_count: int = 0
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _IndexInfo:
    version: str
    model: str
    provider: str
    dim: int
    normalize: bool
    chunk_count: int


@contextmanager
def _connection(conn: psycopg.Connection | None) -> Iterator[psycopg.Connection]:
    if conn is not None:
        yield conn
    else:
        with connect() as own:
            yield own


@lru_cache(maxsize=1)
def _catalogue() -> dict[str, Work]:
    from app.corpus.catalog import load_works

    return load_works()


@lru_cache(maxsize=1)
def _default_embedder() -> Embedder:
    return get_embedder(get_settings())


def _index_info(conn: psycopg.Connection, settings: Settings) -> _IndexInfo:
    """The version the API serves: ACTIVE_INDEX_VERSION if set, else the active flag."""
    columns = (
        "index_version, embedding_model, embedding_provider, embedding_dim, "
        "embedding_normalize, chunk_count"
    )
    if settings.active_index_version:
        row = conn.execute(
            f"SELECT {columns} FROM index_versions WHERE index_version = %s",
            (settings.active_index_version,),
        ).fetchone()
        if row is None:
            raise IndexNotReadyError(
                f"ACTIVE_INDEX_VERSION={settings.active_index_version} no existe en la base."
            )
    else:
        row = conn.execute(f"SELECT {columns} FROM index_versions WHERE active").fetchone()
        if row is None:
            raise IndexNotReadyError("No hay índice activo: correr `make index`.")
    return _IndexInfo(*row)


def current_index_version(conn: psycopg.Connection | None = None) -> str:
    with _connection(conn) as c:
        return _index_info(c, get_settings()).version


def check_active_index(
    conn: psycopg.Connection | None = None, settings: Settings | None = None
) -> IndexCheck:
    """Rule 4: the served index must have the configured embedding model and dimension."""
    s = settings or get_settings()
    try:
        with _connection(conn) as c:
            info = _index_info(c, s)
            stored = c.execute(
                "SELECT count(*) FROM chunks WHERE index_version = %s", (info.version,)
            ).fetchone()[0]
    except (IndexNotReadyError, psycopg.Error) as exc:
        # Leave a caller's connection usable after a failed statement (e.g. no tables yet).
        if conn is not None and conn.info.transaction_status == TransactionStatus.INERROR:
            conn.rollback()
        return IndexCheck(ok=False, problems=[str(exc)])
    check = IndexCheck(
        ok=True,
        index_version=info.version,
        embedding_model=info.model,
        embedding_provider=info.provider,
        embedding_dim=info.dim,
        chunk_count=stored,
    )
    expected = expected_model_name(s)
    if info.model != expected:
        check.problems.append(f"modelo del índice {info.model}, configuración {expected}")
    if info.dim != s.embedding_dim:
        check.problems.append(f"dimensión del índice {info.dim}, configuración {s.embedding_dim}")
    if stored != info.chunk_count or stored == 0:
        check.problems.append(f"el índice tiene {stored} fragmentos y declara {info.chunk_count}")
    if info.provider != s.embedding_provider:
        check.warnings.append(
            f"índice embebido con {info.provider}, consultas con {s.embedding_provider}"
        )
    if info.normalize != s.embedding_normalize:
        check.warnings.append("la normalización del índice no coincide con la configuración")
    check.ok = not check.problems
    return check


def ensure_index_ready(
    conn: psycopg.Connection | None = None, settings: Settings | None = None
) -> IndexCheck:
    """check_active_index() that raises IndexNotReadyError; for backend startup."""
    check = check_active_index(conn, settings)
    if not check.ok:
        raise IndexNotReadyError("Índice no utilizable: " + "; ".join(check.problems))
    return check


def _row_to_chunk(row: tuple) -> Chunk:
    (chunk_id, unit_id, work_id, edition_id, lang, divisions, seq, b_start, b_end, precision,
     text, embed_text) = row[:12]  # fmt: skip
    return Chunk(
        chunk_id=chunk_id,
        unit_id=unit_id,
        work_id=work_id,
        edition_id=edition_id,
        lang=lang,
        divisions=[tuple(d) for d in divisions],
        seq=seq,
        bekker_start=b_start,
        bekker_end=b_end,
        bekker_precision=precision,
        text=text,
        embed_text=embed_text,
    )


def get_chunk(chunk_id: str, conn: psycopg.Connection | None = None) -> Chunk | None:
    with _connection(conn) as c:
        version = _index_info(c, get_settings()).version
        row = c.execute(
            f"SELECT {_CHUNK_COLUMNS} FROM chunks WHERE index_version = %s AND chunk_id = %s",
            (version, chunk_id),
        ).fetchone()
    return _row_to_chunk(row) if row else None


def neighbours(
    chunk_id: str, conn: psycopg.Connection | None = None, window: int = 1
) -> list[Chunk]:
    """Chunks within `window` positions before and after `chunk_id` in its edition, in
    reading order, without the chunk itself. May cross into the previous or next unit."""
    with _connection(conn) as c:
        version = _index_info(c, get_settings()).version
        rows = c.execute(
            f"""
            SELECT {", ".join("x." + name for name in _COLUMN_NAMES)}
            FROM chunks x
            JOIN chunks c ON c.index_version = x.index_version AND c.edition_id = x.edition_id
            WHERE x.index_version = %s AND c.chunk_id = %s AND x.chunk_id <> c.chunk_id
              AND x.seq BETWEEN c.seq - %s AND c.seq + %s
            ORDER BY x.seq
            """,
            (version, chunk_id, window, window),
        ).fetchall()
    return [_row_to_chunk(r) for r in rows]


# --- retrieve --------------------------------------------------------------------------


def _overlap_lines(a: Chunk, b: Chunk) -> int:
    lo = max(a.bekker_start, b.bekker_start)
    hi = min(a.bekker_end, b.bekker_end)
    return bekker.line_distance(lo, hi) + 1 if lo <= hi else 0


class _Deduper:
    """Collects hits in rank order, merging those at the same canonical location."""

    def __init__(self, query_lang: str, min_overlap_lines: int) -> None:
        self.query_lang = query_lang
        self.min_overlap = min_overlap_lines
        self.seen: set[str] = set()
        self.groups: list[dict] = []  # {"rep": (chunk, score, method), "members": [Chunk]}

    def __len__(self) -> int:
        return len(self.groups)

    def _same_location(self, chunk: Chunk, members: list[Chunk]) -> bool:
        if chunk.bekker_start is None or chunk.edition_id in {m.edition_id for m in members}:
            return False
        return any(
            m.work_id == chunk.work_id
            and m.bekker_start is not None
            and _overlap_lines(m, chunk) >= self.min_overlap
            for m in members
        )

    def add(self, chunk: Chunk, score: float, method: str) -> None:
        if chunk.chunk_id in self.seen:
            return
        self.seen.add(chunk.chunk_id)
        for group in self.groups:
            if self._same_location(chunk, group["members"]):
                group["members"].append(chunk)
                rep_lang = group["rep"][0].lang.value
                if chunk.lang.value == self.query_lang and rep_lang != self.query_lang:
                    group["rep"] = (chunk, score, method)
                return
        self.groups.append({"rep": (chunk, score, method), "members": [chunk]})

    def results(self, n: int) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(chunk=chunk, score=score, rank=i, method=method)
            for i, (chunk, score, method) in enumerate((g["rep"] for g in self.groups[:n]), start=1)
        ]


@dataclass
class _Filters:
    version: str
    works: list[str] | None
    lang: str | None
    excluded: list[str]

    def sql(self) -> tuple[str, list]:
        clauses = ["index_version = %s"]
        params: list = [self.version]
        if self.works is not None:
            clauses.append("work_id = ANY(%s)")
            params.append(list(self.works))
        if self.lang:
            clauses.append("lang = %s")
            params.append(self.lang)
        if self.excluded:
            clauses.append("NOT (work_id = ANY(%s))")
            params.append(self.excluded)
        return " AND ".join(clauses), params


def _by_bekker(
    conn: psycopg.Connection,
    filters: _Filters,
    refs: list[tuple[int, int]],
    n: int,
    dedup: _Deduper,
) -> None:
    """Chunks overlapping each reference in Bekker order, until `dedup` holds n locations.
    Each reference reads at most n * _FETCH_FACTOR rows (room for language twins)."""
    where, params = filters.sql()
    for start, end in refs:
        rows = conn.execute(
            f"""
            SELECT {_CHUNK_COLUMNS} FROM chunks
            WHERE {where} AND bekker_start IS NOT NULL
              AND bekker_start <= %s AND bekker_end >= %s
            ORDER BY bekker_start, edition_id, seq
            LIMIT %s
            """,
            [*params, end, start, n * _FETCH_FACTOR],
        ).fetchall()
        for row in rows:
            dedup.add(_row_to_chunk(row), 1.0, "bekker_ref")
        if len(dedup) >= n:
            break


def _dense(
    conn: psycopg.Connection,
    filters: _Filters,
    query_vector: np.ndarray,
    n: int,
    dedup: _Deduper,
) -> None:
    """Exact cosine search, page by page, until `dedup` holds n locations or rows run out."""
    where, params = filters.sql()
    page = n * _FETCH_FACTOR
    offset = 0
    while len(dedup) < n:
        rows = conn.execute(
            f"""
            SELECT {_CHUNK_COLUMNS}, 1 - (embedding <=> %s) AS score FROM chunks
            WHERE {where}
            ORDER BY embedding <=> %s, chunk_id
            LIMIT %s OFFSET %s
            """,
            [query_vector, *params, query_vector, page, offset],
        ).fetchall()
        for row in rows:
            dedup.add(_row_to_chunk(row), float(row[12]), "dense")
        if len(rows) < page:
            break
        offset += page


def retrieve(
    query: str,
    *,
    k: int | None = None,
    works: list[str] | None = None,
    lang: str | None = None,
    include_dubious: bool = True,
    conn: psycopg.Connection | None = None,
    embedder: Embedder | None = None,
    candidates: int | None = None,
) -> list[RetrievedChunk]:
    """At most k hits (settings.retrieval_k), ranked 1..k; `candidates` overrides k."""
    s = get_settings()
    n = candidates if candidates is not None else (k if k is not None else s.retrieval_k)
    if n <= 0:
        return []
    catalogue = _catalogue()
    excluded = (
        []
        if include_dubious
        else sorted(w for w, work in catalogue.items() if work.authenticity != Authenticity.genuine)
    )
    query_lang = str(lang) if lang else detect_lang(query)
    dedup = _Deduper(query_lang, s.dedup_min_overlap_lines)

    with _connection(conn) as c:
        info = _index_info(c, s)
        filters = _Filters(info.version, works, str(lang) if lang else None, excluded)
        # Rule 4, before any path (the Bekker one needs no embedding but must not serve an
        # index built with another model either).
        embedder = embedder or _default_embedder()
        if embedder.name != info.model or embedder.dim != info.dim:
            raise IndexNotReadyError(
                f"El índice {info.version} usa {info.model} ({info.dim} dim) y las consultas "
                f"se embeberían con {embedder.name} ({embedder.dim} dim)."
            )

        refs = list(dict.fromkeys(bekker.find_refs(query)))[: s.retrieval_max_bekker_refs]
        if refs:
            _by_bekker(c, filters, refs, n, dedup)
            if len(dedup):
                return dedup.results(n)

        query_vector = np.asarray(embedder.embed_query(query), dtype=np.float32)

        detected = [] if works is not None else detect_works(query, catalogue)
        detected = [w for w in detected if w not in excluded]
        if detected:
            named = _Filters(info.version, detected, filters.lang, excluded)
            _dense(c, named, query_vector, n, dedup)
        if len(dedup) < n:
            _dense(c, filters, query_vector, n, dedup)
    return dedup.results(n)
