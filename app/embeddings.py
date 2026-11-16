"""Embedding clients: Ollama, OpenAI-compatible APIs, and a deterministic fake.

The model is the same everywhere (rule 4): `name` is its canonical name (BAAI/bge-m3),
stored with the index, and `provider_model` is the id each provider uses for it
(`bge-m3` in Ollama, `@cf/baai/bge-m3` in Workers AI, `BAAI/bge-m3` in DeepInfra).

Vectors are L2-normalised when settings.embedding_normalize is on, and every vector is
checked against settings.embedding_dim. Input length is not checked here: chunking keeps
texts under chunk_max_tokens, because Ollama truncates silently by default (we ask it not
to) and Workers AI returns an error.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
import unicodedata

import httpx
import numpy as np

from app.settings import Settings, get_settings

FAKE_MODEL_NAME = "fake/hash-embedder-v1"
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRY_AFTER_S = 30.0


class EmbeddingError(RuntimeError):
    """The provider failed or returned something unusable. Message is for the person."""


class EmbeddingUnavailableError(EmbeddingError):
    """The provider could not be reached at all (connection refused, DNS, proxy)."""


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


class Embedder:
    """Base class: batching, dimension check and normalisation around `_embed_batch`."""

    provider = "base"

    def __init__(self, *, name: str, dim: int, normalize: bool, batch_size: int) -> None:
        self.name = name
        self.dim = dim
        self.normalize = normalize
        self.batch_size = max(1, batch_size)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            vectors = self._embed_batch(batch)
            if len(vectors) != len(batch):
                raise EmbeddingError(
                    f"{self.provider}: se pidieron {len(batch)} embeddings y llegaron "
                    f"{len(vectors)}."
                )
            array = np.asarray(vectors, dtype=np.float64)
            if array.ndim != 2 or array.shape[1] != self.dim:
                got = array.shape[1] if array.ndim == 2 else array.shape
                raise EmbeddingError(
                    f"{self.provider}: dimensión {got} y la configuración espera "
                    f"{self.dim} (EMBEDDING_DIM). ¿Es el mismo modelo?"
                )
            if self.normalize:
                array = l2_normalize(array)
            out.extend(array.tolist())
        return out

    def embed_query(self, text: str) -> list[float]:
        # bge-m3 uses no query instruction, so a query embeds like a document.
        return self.embed_documents([text])[0]


class _HttpEmbedder(Embedder):
    def __init__(
        self,
        *,
        base_url: str,
        provider_model: str,
        api_key: str = "",
        timeout_s: float = 60.0,
        max_retries: int = 3,
        backoff_s: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")
        self.provider_model = provider_model
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(timeout=timeout_s, headers=headers, transport=transport)

    def _post(self, url: str, payload: dict) -> dict:
        """POST with retries on 429, 5xx and timeouts (exponential backoff, Retry-After)."""
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                response = self._client.post(url, json=payload)
            except httpx.ConnectError as exc:
                raise EmbeddingUnavailableError(
                    f"No se pudo conectar con {self.provider} en {self.base_url}: {exc}"
                ) from exc
            except httpx.TimeoutException as exc:
                if last:
                    raise EmbeddingError(f"{self.provider} no respondió a tiempo ({url}).") from exc
                self._wait(attempt, None)
                continue
            except httpx.HTTPError as exc:
                raise EmbeddingUnavailableError(
                    f"Error de red con {self.provider} en {self.base_url}: {exc}"
                ) from exc
            if response.status_code in _RETRY_STATUS and not last:
                self._wait(attempt, response.headers.get("retry-after"))
                continue
            if response.status_code >= 400:
                raise EmbeddingError(
                    f"{self.provider} devolvió HTTP {response.status_code} en {url}: "
                    f"{response.text[:300]}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise EmbeddingError(f"{self.provider} devolvió algo que no es JSON.") from exc
        raise AssertionError("unreachable")

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        delay = self.backoff_s * (2**attempt)
        if retry_after and retry_after.replace(".", "", 1).isdigit():
            delay = max(delay, float(retry_after))
        time.sleep(min(delay, _MAX_RETRY_AFTER_S))


class OllamaEmbedder(_HttpEmbedder):
    """Ollama's POST /api/embed. `truncate: false` makes over-long inputs an error."""

    provider = "ollama"

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        data = self._post(
            f"{self.base_url}/api/embed",
            {"model": self.provider_model, "input": texts, "truncate": False},
        )
        vectors = data.get("embeddings")
        if not isinstance(vectors, list):
            raise EmbeddingError("Ollama: la respuesta no trae 'embeddings'.")
        return vectors


class OpenAICompatEmbedder(_HttpEmbedder):
    """POST {base}/embeddings in the OpenAI format.

    Works with Cloudflare Workers AI (base .../accounts/<id>/ai/v1, model @cf/baai/bge-m3)
    and DeepInfra (base https://api.deepinfra.com/v1/openai, model BAAI/bge-m3).
    """

    provider = "openai_compat"

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        data = self._post(
            f"{self.base_url}/embeddings", {"model": self.provider_model, "input": texts}
        )
        items = data.get("data")
        if not isinstance(items, list):
            raise EmbeddingError("Proveedor OpenAI-compatible: la respuesta no trae 'data'.")
        items = sorted(items, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in items]


_TOKEN_RE = re.compile(r"\w+")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


class FakeEmbedder(Embedder):
    """Deterministic embedder for tests: hashes words into the vector.

    Each word adds a signed weight to two buckets chosen by its sha256, so texts that share
    words get similar vectors. Lexical only: it says nothing about real semantic quality.
    """

    provider = "fake"

    def __init__(self, *, dim: int, normalize: bool = True, batch_size: int = 64) -> None:
        super().__init__(name=FAKE_MODEL_NAME, dim=dim, normalize=normalize, batch_size=batch_size)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = np.zeros(self.dim)
        counts: dict[str, int] = {}
        for token in _TOKEN_RE.findall(_fold(text)):
            counts[token] = counts.get(token, 0) + 1
        if not counts:
            counts[""] = 1  # empty text still gets a fixed non-zero vector
        for token, count in counts.items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            weight = 1.0 + math.log(count)
            for k in range(2):
                bucket = int.from_bytes(digest[4 * k : 4 * k + 4], "big") % self.dim
                sign = 1.0 if digest[8 + k] % 2 == 0 else -1.0
                vector[bucket] += sign * weight
        return vector.tolist()


def config_problems(settings: Settings | None = None) -> list[str]:
    """What is missing for get_embedder() to build a usable client (for /readyz). Checks
    the configuration only; it does not call the provider."""
    from app.llm import _is_local  # same notion of a local endpoint as the LLM check

    s = settings or get_settings()
    if s.embedding_provider == "fake":
        return ["EMBEDDING_PROVIDER=fake en producción"] if s.env == "prod" else []
    if s.embedding_provider not in ("ollama", "openai_compat"):
        return [
            f"EMBEDDING_PROVIDER desconocido: {s.embedding_provider!r} "
            "(opciones: ollama, openai_compat, fake)"
        ]
    if not s.embedding_base_url or not s.embedding_provider_model:
        return ["falta EMBEDDING_BASE_URL o EMBEDDING_PROVIDER_MODEL"]
    if (
        s.embedding_provider == "openai_compat"
        and not s.embedding_api_key
        and not _is_local(s.embedding_base_url)
    ):
        return ["falta EMBEDDING_API_KEY"]
    return []


def expected_model_name(settings: Settings | None = None) -> str:
    """Canonical model name an index must have to be served with these settings."""
    s = settings or get_settings()
    return FAKE_MODEL_NAME if s.embedding_provider == "fake" else s.embedding_model


def get_embedder(settings: Settings | None = None) -> Embedder:
    """Embedder selected by settings.embedding_provider: ollama, openai_compat or fake."""
    s = settings or get_settings()
    common = {"dim": s.embedding_dim, "normalize": s.embedding_normalize}
    if s.embedding_provider == "fake":
        return FakeEmbedder(**common, batch_size=s.embedding_batch_size)
    http = {
        "name": s.embedding_model,
        "batch_size": s.embedding_batch_size,
        "base_url": s.embedding_base_url,
        "provider_model": s.embedding_provider_model,
        "api_key": s.embedding_api_key,
        "timeout_s": s.embedding_timeout_s,
        "max_retries": s.embedding_max_retries,
    }
    if s.embedding_provider == "ollama":
        return OllamaEmbedder(**common, **http)
    if s.embedding_provider == "openai_compat":
        if not s.embedding_base_url:
            raise EmbeddingError("Falta EMBEDDING_BASE_URL para el proveedor openai_compat.")
        return OpenAICompatEmbedder(**common, **http)
    raise EmbeddingError(
        f"Proveedor de embeddings desconocido: {s.embedding_provider!r} "
        "(opciones: ollama, openai_compat, fake)."
    )
