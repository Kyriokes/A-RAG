"""`make parity`: do Ollama and the production provider give the same embeddings?

Embeds the same chunks and queries with both and passes when the mean cosine between the
two vectors of each text is at least settings.parity_min_cosine and every query gets the
same top settings.parity_top_k chunks (as a set) from both (PLAN, Etapa 2). If it does not
pass, the local index is also embedded with the production provider and the decision goes
to docs/decisions.md.

Exit codes: 0 passes, 1 does not pass, 2 could not run (provider unreachable, missing
configuration or corpus).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np

from app.chunking import chunk_units
from app.embeddings import (
    Embedder,
    EmbeddingError,
    EmbeddingUnavailableError,
    OllamaEmbedder,
    OpenAICompatEmbedder,
)
from app.index import IndexBuildError, load_indexed_corpus
from app.settings import Settings, get_settings

# Fixed probe queries, written for this check (not taken from the eval dataset).
QUERIES = [
    "¿Qué es la felicidad según Aristóteles?",
    "¿Cómo se adquiere la virtud moral?",
    "¿Qué es el término medio?",
    "¿Cuántas clases de amistad hay?",
    "¿Qué diferencia hay entre lo voluntario y lo involuntario?",
    "¿Qué es la prudencia?",
    "¿Qué relación hay entre el placer y la actividad?",
    "¿Qué es la justicia distributiva?",
    "¿Por qué la vida contemplativa es la más feliz?",
    "¿Qué es la incontinencia?",
    "What is the highest good for human beings?",
    "How does Aristotle define courage?",
    "What is the role of habit in moral education?",
    "Is pleasure the good?",
    "What is the difference between justice and equity?",
    "How are friendships of utility different from friendships of virtue?",
    "What does Aristotle say about magnanimity?",
    "Why is politics the master science?",
    "What is choice and how does it relate to deliberation?",
    "Can a person be harmed after death?",
]


class ParityError(RuntimeError):
    """The comparison could not run. Message is for the person."""


@dataclass
class ParityReport:
    n_texts: int
    n_queries: int
    top_k: int
    mean_cosine: float
    min_cosine: float
    min_required: float
    mismatched_queries: list[int]  # indexes of queries whose top-k sets differ

    @property
    def passed(self) -> bool:
        return self.mean_cosine >= self.min_required and not self.mismatched_queries


def _unit_rows(vectors: list[list[float]]) -> np.ndarray:
    array = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def compare(
    docs_a: list[list[float]],
    docs_b: list[list[float]],
    queries_a: list[list[float]],
    queries_b: list[list[float]],
    *,
    top_k: int,
    min_cosine: float,
) -> ParityReport:
    """Compare two providers' vectors for the same chunks (docs) and queries."""
    da, db = _unit_rows(docs_a), _unit_rows(docs_b)
    qa, qb = _unit_rows(queries_a), _unit_rows(queries_b)
    if da.shape != db.shape or qa.shape != qb.shape:
        raise ValueError(
            f"shapes differ: docs {da.shape} vs {db.shape}, queries {qa.shape} vs {qb.shape}"
        )
    # Cosine between the two vectors of each text, chunks and queries alike.
    pair_cos = np.concatenate([(da * db).sum(axis=1), (qa * qb).sum(axis=1)])
    k = min(top_k, da.shape[0])
    mismatched = []
    for i in range(qa.shape[0]):
        top_a = set(np.argsort(-(da @ qa[i]), kind="stable")[:k].tolist())
        top_b = set(np.argsort(-(db @ qb[i]), kind="stable")[:k].tolist())
        if top_a != top_b:
            mismatched.append(i)
    return ParityReport(
        n_texts=da.shape[0],
        n_queries=qa.shape[0],
        top_k=k,
        mean_cosine=float(pair_cos.mean()),
        min_cosine=float(pair_cos.min()),
        min_required=min_cosine,
        mismatched_queries=mismatched,
    )


def sample_evenly(items: list, n: int) -> list:
    """n items spread evenly over the list (deterministic)."""
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def _embedders(s: Settings) -> tuple[Embedder, Embedder]:
    common = {
        "name": s.embedding_model,
        "dim": s.embedding_dim,
        "normalize": s.embedding_normalize,
        "batch_size": s.embedding_batch_size,
        "timeout_s": s.embedding_timeout_s,
        "max_retries": s.embedding_max_retries,
    }
    ollama = OllamaEmbedder(
        base_url=s.parity_ollama_base_url, provider_model=s.parity_ollama_model, **common
    )
    prod = OpenAICompatEmbedder(
        base_url=s.parity_prod_base_url,
        provider_model=s.parity_prod_model,
        api_key=s.parity_prod_api_key,
        **common,
    )
    return ollama, prod


def _embed_all(
    embedder: Embedder, label: str, docs: list[str], queries: list[str]
) -> tuple[list[list[float]], list[list[float]]]:
    try:
        return embedder.embed_documents(docs), [embedder.embed_query(q) for q in queries]
    except EmbeddingUnavailableError as exc:
        raise ParityError(f"no se pudo conectar con {label}. {exc}") from exc
    except EmbeddingError as exc:
        raise ParityError(f"{label} falló: {exc}") from exc


def _fail(message: str) -> int:
    print(f"make parity no pudo correr: {message}", file=sys.stderr)
    return 2


def main() -> int:
    s = get_settings()
    if not s.parity_prod_base_url:
        return _fail(
            "falta PARITY_PROD_BASE_URL (endpoint OpenAI-compatible del proveedor de "
            "producción, p. ej. Workers AI) y su clave en PARITY_PROD_API_KEY."
        )
    try:
        works, _, units, _ = load_indexed_corpus()
    except IndexBuildError as exc:
        return _fail(str(exc))
    chunks = chunk_units(units, works, s)
    docs = [c.embed_text for c in sample_evenly(chunks, s.parity_n_chunks)]
    queries = QUERIES[: s.parity_n_queries]

    ollama, prod = _embedders(s)
    ollama_label = f"Ollama ({s.parity_ollama_base_url}, modelo {s.parity_ollama_model})"
    prod_label = f"el proveedor de producción ({s.parity_prod_base_url}, {s.parity_prod_model})"
    try:
        docs_a, queries_a = _embed_all(ollama, ollama_label, docs, queries)
        docs_b, queries_b = _embed_all(prod, prod_label, docs, queries)
    except ParityError as exc:
        return _fail(str(exc))
    report = compare(
        docs_a, docs_b, queries_a, queries_b, top_k=s.parity_top_k, min_cosine=s.parity_min_cosine
    )

    print(f"Paridad de embeddings: {ollama_label} vs {prod_label}")
    print(f"Textos: {report.n_texts} fragmentos + {report.n_queries} consultas")
    print(
        f"Coseno medio: {report.mean_cosine:.4f} (mínimo {report.min_cosine:.4f}; "
        f"umbral {report.min_required})"
    )
    same = report.n_queries - len(report.mismatched_queries)
    print(f"Top-{report.top_k} iguales: {same}/{report.n_queries}")
    for i in report.mismatched_queries:
        print(f"  distinto: {queries[i]}")
    print("Resultado: PASA" if report.passed else "Resultado: NO PASA")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
