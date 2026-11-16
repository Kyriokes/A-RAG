"""`make index`: chunk the indexed editions, embed them and switch index versions atomically.

index_version is a hash of the corpus version, the chunking config and the embedding model
(canonical name, dimension, normalisation). The provider is stored with the version but is
not part of the hash, so a local index (Ollama) and the production one (Workers AI) built
from the same inputs share a version, as the plan's Etapa 7 comparison expects; `make
parity` is what makes that sound.

The build is idempotent: the same inputs give the same version and running it again prints
"sin cambios". A new version is written and validated completely and only then made active,
all in one transaction, so readers never see a half-built index. Old versions stay in the
database for rollback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from app.chunking import ChunkTooLongError, chunk_units, chunking_config
from app.db import connect
from app.embeddings import Embedder, EmbeddingError, expected_model_name, get_embedder
from app.models import Chunk, Edition, SourceStatus, TextType, Unit, Work
from app.settings import Settings, get_settings

_TS_CONFIG = {"en": "english", "es": "spanish"}


class IndexBuildError(RuntimeError):
    """The index cannot be built or activated. Message is for the person."""


@dataclass
class IndexResult:
    index_version: str
    chunk_count: int
    status: str  # "created" | "unchanged" | "reactivated" | "rebuilt"
    embedded: int  # texts sent to the provider (cache misses)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_corpus_version(editions: list[Edition], units: list[Unit]) -> str:
    """Hash of the indexed manifest rows and their units.

    Fallback for callers that pass units directly (tests); `make index` uses
    app.corpus.catalog.corpus_version(), the hash of the catalogue, the manifest and the
    processed files.
    """
    h = hashlib.sha256()
    for edition in sorted(editions, key=lambda e: e.edition_id):
        h.update(json.dumps(edition.model_dump(mode="json"), sort_keys=True).encode())
    for unit in sorted(units, key=lambda u: (u.edition_id, u.seq, u.unit_id)):
        h.update(json.dumps(unit.model_dump(mode="json"), sort_keys=True).encode())
    return h.hexdigest()[:16]


def compute_index_version(
    corpus_version: str, chunking: dict, model: str, dim: int, normalize: bool
) -> str:
    payload = {
        "corpus_version": corpus_version,
        "chunking": chunking,
        "embedding": {"model": model, "dim": dim, "normalize": normalize},
    }
    return _sha256(json.dumps(payload, sort_keys=True))[:16]


def build_index(
    units: list[Unit],
    *,
    works: dict[str, Work],
    editions: list[Edition],
    conn: psycopg.Connection,
    embedder: Embedder,
    settings: Settings | None = None,
    corpus_version: str | None = None,
    rebuild: bool = False,
    log: Callable[[str], None] = print,
) -> IndexResult:
    """Build (or reuse) the index version for these inputs and make it the active one."""
    s = settings or get_settings()
    expected = expected_model_name(s)
    if embedder.name != expected or embedder.dim != s.embedding_dim:
        raise IndexBuildError(
            f"No se activa el índice: el embedder es {embedder.name} ({embedder.dim} dim) y la "
            f"configuración espera {expected} ({s.embedding_dim} dim)."
        )

    indexed = [e for e in editions if e.status == SourceStatus.indexed]
    indexed_ids = {e.edition_id for e in indexed}
    units = [u for u in units if u.edition_id in indexed_ids and u.text_type == TextType.aristotle]
    if not units:
        raise IndexBuildError("No hay unidades para indexar: ¿se corrió `make corpus`?")

    chunks = chunk_units(units, works, s)
    duplicated = [cid for cid, n in Counter(c.chunk_id for c in chunks).items() if n > 1]
    if duplicated:
        raise IndexBuildError(f"chunk_id repetidos: {duplicated[:5]}")
    missing = indexed_ids - {c.edition_id for c in chunks}
    if missing:
        raise IndexBuildError(f"Ediciones indexadas sin fragmentos: {sorted(missing)}")

    corpus_version = corpus_version or compute_corpus_version(indexed, units)
    chunking = chunking_config(s)
    version = compute_index_version(
        corpus_version, chunking, embedder.name, embedder.dim, embedder.normalize
    )

    existing = conn.execute(
        "SELECT embedding_provider, chunk_count, active FROM index_versions "
        "WHERE index_version = %s",
        (version,),
    ).fetchone()
    if existing and not rebuild:
        provider, chunk_count, active = existing
        if provider != embedder.provider:
            raise IndexBuildError(
                f"La versión {version} ya existe embebida con {provider}. Para reembeberla "
                f"con {embedder.provider}: python -m app.index --rebuild"
            )
        stored = conn.execute(
            "SELECT count(*) FROM chunks WHERE index_version = %s", (version,)
        ).fetchone()[0]
        if stored != chunk_count or stored != len(chunks):
            raise IndexBuildError(
                f"La versión {version} está incompleta ({stored} de {len(chunks)} fragmentos). "
                "Reconstruirla: python -m app.index --rebuild"
            )
        if active:
            conn.commit()
            return IndexResult(version, len(chunks), "unchanged", 0)
        conn.commit()
        with conn.transaction():
            _activate(conn, version)
        _warn_pinned(s, version, log)
        return IndexResult(version, len(chunks), "reactivated", 0)

    vectors, embedded = _embed_with_cache(conn, chunks, embedder)

    conn.commit()  # the block below must be a real transaction, not a savepoint
    with conn.transaction():
        if existing:
            conn.execute("DELETE FROM index_versions WHERE index_version = %s", (version,))
        conn.execute(
            """
            INSERT INTO index_versions (index_version, embedding_model, embedding_provider,
                embedding_dim, embedding_normalize, corpus_version, chunking_config,
                chunk_count, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false)
            """,
            (
                version,
                embedder.name,
                embedder.provider,
                embedder.dim,
                embedder.normalize,
                corpus_version,
                Jsonb(chunking),
                len(chunks),
            ),
        )
        _insert_chunks(conn, version, chunks, vectors)
        stored = conn.execute(
            "SELECT count(*) FROM chunks WHERE index_version = %s", (version,)
        ).fetchone()[0]
        if stored != len(chunks) or stored == 0:
            raise IndexBuildError(
                f"Conteo inválido para {version}: {stored} guardados, {len(chunks)} esperados."
            )
        _activate(conn, version)
    _warn_pinned(s, version, log)
    return IndexResult(version, len(chunks), "rebuilt" if existing else "created", embedded)


def _activate(conn: psycopg.Connection, version: str) -> None:
    """Make `version` the only active one. Call inside a transaction."""
    conn.execute("UPDATE index_versions SET active = false WHERE active")
    conn.execute(
        "UPDATE index_versions SET active = true, activated_at = now() WHERE index_version = %s",
        (version,),
    )


def _warn_pinned(s: Settings, version: str, log: Callable[[str], None]) -> None:
    if s.active_index_version and s.active_index_version != version:
        log(
            f"Aviso: ACTIVE_INDEX_VERSION={s.active_index_version} fija la versión que sirve la "
            f"API; {version} quedó activa en la base, pero la API seguirá con la fijada."
        )


def _embed_with_cache(
    conn: psycopg.Connection, chunks: list[Chunk], embedder: Embedder
) -> tuple[list[np.ndarray], int]:
    """Vectors for every chunk, from the cache when possible. Returns (vectors, n embedded)."""
    shas = [_sha256(c.embed_text) for c in chunks]
    cached: dict[str, np.ndarray] = {}
    unique = sorted(set(shas))
    key = (embedder.name, embedder.provider, embedder.normalize, embedder.dim)
    for i in range(0, len(unique), 1000):
        rows = conn.execute(
            """
            SELECT text_sha256, embedding FROM embedding_cache
            WHERE model = %s AND provider = %s AND normalized = %s AND dim = %s
              AND text_sha256 = ANY(%s)
            """,
            (*key, unique[i : i + 1000]),
        ).fetchall()
        cached.update(rows)
    conn.commit()

    text_by_sha = {sha: c.embed_text for sha, c in zip(shas, chunks, strict=True)}
    misses = [sha for sha in unique if sha not in cached]
    for i in range(0, len(misses), embedder.batch_size):
        batch = misses[i : i + embedder.batch_size]
        vectors = embedder.embed_documents([text_by_sha[sha] for sha in batch])
        rows = []
        for sha, vector in zip(batch, vectors, strict=True):
            array = np.asarray(vector, dtype=np.float32)
            cached[sha] = array
            rows.append(
                (embedder.name, sha, embedder.provider, embedder.normalize, embedder.dim, array)
            )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO embedding_cache (model, text_sha256, provider, normalized, dim,
                    embedding)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (model, text_sha256) DO UPDATE SET provider = EXCLUDED.provider,
                    normalized = EXCLUDED.normalized, dim = EXCLUDED.dim,
                    embedding = EXCLUDED.embedding, created_at = now()
                """,
                rows,
            )
        conn.commit()  # keep what was paid for even if a later batch fails
    return [cached[sha] for sha in shas], len(misses)


def _insert_chunks(
    conn: psycopg.Connection, version: str, chunks: list[Chunk], vectors: list[np.ndarray]
) -> None:
    rows = [
        (
            version,
            c.chunk_id,
            c.unit_id,
            c.work_id,
            c.edition_id,
            c.lang.value,
            Jsonb([list(d) for d in c.divisions]),
            c.seq,
            c.bekker_start,
            c.bekker_end,
            c.bekker_precision.value,
            c.text,
            c.embed_text,
            _sha256(c.embed_text),
            _TS_CONFIG.get(c.lang.value, "simple"),
            vector,
        )
        for c, vector in zip(chunks, vectors, strict=True)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks (index_version, chunk_id, unit_id, work_id, edition_id, lang,
                divisions, seq, bekker_start, bekker_end, bekker_precision, text, embed_text,
                embed_sha256, ts_config, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::regconfig, %s)
            """,
            rows,
        )


def load_indexed_corpus() -> tuple[dict[str, Work], list[Edition], list[Unit], str]:
    """Catalogue, manifest, units of every `indexed` edition, and the corpus version."""
    from app.corpus import catalog

    try:
        works = catalog.load_works()
        editions = catalog.load_sources()
        units: list[Unit] = []
        for edition in editions:
            if edition.status == SourceStatus.indexed:
                units.extend(catalog.load_units(edition.edition_id))
        return works, editions, units, catalog.corpus_version()
    except FileNotFoundError as exc:
        raise IndexBuildError(
            f"Falta un archivo del corpus ({exc}): correr `make corpus`."
        ) from exc
    except ValueError as exc:  # CatalogError, or a malformed row
        raise IndexBuildError(f"Catálogo o manifiesto inválido: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chunking + embeddings + carga en Postgres.")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="reembeber y reescribir la versión aunque ya exista (p. ej., otro proveedor)",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        works, editions, units, corpus_version = load_indexed_corpus()
        embedder = get_embedder(settings)
        with connect(settings) as conn:
            result = build_index(
                units,
                works=works,
                editions=editions,
                conn=conn,
                embedder=embedder,
                settings=settings,
                corpus_version=corpus_version,
                rebuild=args.rebuild,
            )
    except (IndexBuildError, EmbeddingError, ChunkTooLongError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        print(f"Error: no se pudo conectar a la base (DATABASE_URL): {exc}", file=sys.stderr)
        return 1
    messages = {
        "unchanged": "sin cambios",
        "created": "nueva versión creada y activada",
        "reactivated": "versión existente reactivada",
        "rebuilt": "versión reconstruida y activada",
    }
    print(f"Fragmentos: {result.chunk_count}")
    print(f"Versión de índice: {result.index_version}")
    print(f"Embebidos ahora: {result.embedded} (el resto vino de la caché)")
    print(messages[result.status])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
