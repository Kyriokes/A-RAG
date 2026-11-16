import json

import httpx
import numpy as np
import pytest

from app import parity
from app.embeddings import (
    FAKE_MODEL_NAME,
    Embedder,
    EmbeddingError,
    EmbeddingUnavailableError,
    FakeEmbedder,
    OllamaEmbedder,
    OpenAICompatEmbedder,
    expected_model_name,
    get_embedder,
)
from app.settings import Settings


def cos(a, b) -> float:
    a, b = np.asarray(a), np.asarray(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


# --- fake -------------------------------------------------------------------------------


def test_fake_is_deterministic_and_normalised():
    e = FakeEmbedder(dim=128)
    a = e.embed_documents(["La amistad perfecta es entre hombres buenos."])[0]
    b = FakeEmbedder(dim=128).embed_query("La amistad perfecta es entre hombres buenos.")
    assert a == b
    assert len(a) == 128
    assert np.linalg.norm(a) == pytest.approx(1.0)


def test_fake_texts_sharing_words_are_closer():
    e = FakeEmbedder(dim=256)
    query, near, far = e.embed_documents(
        [
            "what is courage in battle",
            "courage is shown in battle by the brave soldier",
            "pleasure completes the activity of contemplation",
        ]
    )
    assert cos(query, near) > cos(query, far)


def test_fake_ignores_case_and_accents():
    e = FakeEmbedder(dim=64)
    assert e.embed_query("Ética a Nicómaco") == e.embed_query("etica a nicomaco")


def test_fake_empty_text_has_a_unit_vector():
    v = FakeEmbedder(dim=32).embed_query("")
    assert np.linalg.norm(v) == pytest.approx(1.0)


# --- base class -------------------------------------------------------------------------


class _Fixed(Embedder):
    provider = "test"

    def __init__(self, vectors, **kwargs):
        super().__init__(name="m", **kwargs)
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def _embed_batch(self, texts):
        self.calls.append(texts)
        return [self.vectors[t] for t in texts]


def test_normalisation_is_applied_when_configured():
    e = _Fixed({"a": [3.0, 4.0]}, dim=2, normalize=True, batch_size=8)
    assert e.embed_query("a") == pytest.approx([0.6, 0.8])
    raw = _Fixed({"a": [3.0, 4.0]}, dim=2, normalize=False, batch_size=8)
    assert raw.embed_query("a") == [3.0, 4.0]


def test_wrong_dimension_is_an_error():
    e = _Fixed({"a": [1.0, 0.0, 0.0]}, dim=2, normalize=True, batch_size=8)
    with pytest.raises(EmbeddingError, match="dimensión"):
        e.embed_query("a")


def test_batches_follow_batch_size_and_keep_order():
    vectors = {t: [float(i + 1), 0.0] for i, t in enumerate("abcde")}
    e = _Fixed(vectors, dim=2, normalize=False, batch_size=2)
    out = e.embed_documents(list("abcde"))
    assert e.calls == [["a", "b"], ["c", "d"], ["e"]]
    assert [v[0] for v in out] == [1.0, 2.0, 3.0, 4.0, 5.0]


# --- HTTP clients -----------------------------------------------------------------------


def _ollama(handler, **kwargs) -> OllamaEmbedder:
    return OllamaEmbedder(
        name="BAAI/bge-m3",
        dim=2,
        normalize=True,
        batch_size=kwargs.pop("batch_size", 8),
        base_url="http://ollama.test:11434/",
        provider_model="bge-m3",
        backoff_s=0,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_ollama_request_and_response():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.method, str(request.url), body))
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in body["input"]]})

    out = _ollama(handler).embed_documents(["uno", "dos"])
    method, url, body = seen[0]
    assert (method, url) == ("POST", "http://ollama.test:11434/api/embed")
    assert body == {"model": "bge-m3", "input": ["uno", "dos"], "truncate": False}
    assert out == [pytest.approx([0.6, 0.8])] * 2


def test_openai_compat_request_and_response():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        data = [
            {"object": "embedding", "index": 1, "embedding": [0.0, 2.0]},
            {"object": "embedding", "index": 0, "embedding": [2.0, 0.0]},
        ]
        return httpx.Response(200, json={"object": "list", "data": data})

    e = OpenAICompatEmbedder(
        name="BAAI/bge-m3",
        dim=2,
        normalize=True,
        batch_size=8,
        base_url="https://api.example.test/accounts/x/ai/v1",
        provider_model="@cf/baai/bge-m3",
        api_key="secret-token",
        transport=httpx.MockTransport(handler),
    )
    out = e.embed_documents(["first", "second"])
    request = seen[0]
    assert str(request.url) == "https://api.example.test/accounts/x/ai/v1/embeddings"
    assert request.headers["authorization"] == "Bearer secret-token"
    assert json.loads(request.content) == {"model": "@cf/baai/bge-m3", "input": ["first", "second"]}
    assert out == [[1.0, 0.0], [0.0, 1.0]]  # reordered by index


def test_retries_on_429_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})

    assert _ollama(handler, max_retries=2).embed_query("x") == [1.0, 0.0]
    assert len(calls) == 2


def test_gives_up_after_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="unavailable")

    with pytest.raises(EmbeddingError, match="503"):
        _ollama(handler, max_retries=2).embed_query("x")
    assert len(calls) == 3


def test_client_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text="input too long")

    with pytest.raises(EmbeddingError, match="input too long"):
        _ollama(handler, max_retries=3).embed_query("x")
    assert len(calls) == 1


def test_unreachable_provider():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(EmbeddingUnavailableError, match="No se pudo conectar"):
        _ollama(handler).embed_query("x")


def test_wrong_count_from_provider():
    def handler(request):
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})

    with pytest.raises(EmbeddingError):
        _ollama(handler).embed_documents(["a", "b"])


# --- selection --------------------------------------------------------------------------


def test_get_embedder_by_provider():
    fake = get_embedder(Settings(embedding_provider="fake", embedding_dim=16))
    assert isinstance(fake, FakeEmbedder) and fake.dim == 16
    assert fake.name == FAKE_MODEL_NAME
    ollama = get_embedder(Settings(embedding_provider="ollama"))
    assert isinstance(ollama, OllamaEmbedder) and ollama.name == "BAAI/bge-m3"
    compat = get_embedder(
        Settings(embedding_provider="openai_compat", embedding_base_url="https://x.test/v1")
    )
    assert isinstance(compat, OpenAICompatEmbedder)
    with pytest.raises(EmbeddingError):
        get_embedder(Settings(embedding_provider="nope"))
    with pytest.raises(EmbeddingError):
        get_embedder(Settings(embedding_provider="openai_compat", embedding_base_url=""))


def test_expected_model_name():
    assert expected_model_name(Settings(embedding_provider="fake")) == FAKE_MODEL_NAME
    assert (
        expected_model_name(Settings(embedding_provider="ollama", embedding_model="BAAI/bge-m3"))
        == "BAAI/bge-m3"
    )


# --- make parity (comparison logic; providers faked) -----------------------------------


def _rng_vectors(n: int, dim: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(size=(n, dim))


def test_parity_identical_vectors_pass():
    docs, queries = _rng_vectors(50, 16, 1), _rng_vectors(20, 16, 2)
    report = parity.compare(docs, docs, queries, queries, top_k=5, min_cosine=0.99)
    assert report.mean_cosine == pytest.approx(1.0)
    assert report.mismatched_queries == [] and report.passed


def test_parity_low_cosine_fails():
    docs, queries = _rng_vectors(50, 16, 1), _rng_vectors(20, 16, 2)
    noisy = docs + _rng_vectors(50, 16, 3) * 0.5
    report = parity.compare(docs, noisy, queries, queries, top_k=5, min_cosine=0.99)
    assert report.mean_cosine < 0.99 and not report.passed


def test_parity_top_k_disagreement_fails_even_with_high_cosine():
    docs, queries = _rng_vectors(50, 16, 1), _rng_vectors(20, 16, 2)
    # Swap the 5th and 6th best documents of query 0 in provider B: tiny change overall.
    order = np.argsort(-(docs / np.linalg.norm(docs, axis=1, keepdims=True)) @ queries[0])
    swapped = docs.copy()
    swapped[[order[4], order[5]]] = docs[[order[5], order[4]]]
    report = parity.compare(docs, swapped, queries, queries, top_k=5, min_cosine=0.5)
    assert 0 in report.mismatched_queries and not report.passed


def test_parity_sample_evenly():
    assert parity.sample_evenly(list(range(10)), 5) == [0, 2, 4, 6, 8]
    assert parity.sample_evenly([1, 2], 5) == [1, 2]


def _parity_setup(monkeypatch, **settings_overrides):
    from tests.test_chunking import WORK, paragraph, unit

    units = [unit("\n\n".join(paragraph(f"u{i}p{j}", 60) for j in range(3)), unit_id=f"e:{i}")
             for i in range(30)]  # fmt: skip
    values = {"parity_prod_base_url": "https://prod.test/v1", "parity_n_chunks": 50}
    values.update(settings_overrides)
    monkeypatch.setattr(parity, "get_settings", lambda: Settings(**values))
    monkeypatch.setattr(parity, "load_indexed_corpus", lambda: ({"eth-nic": WORK}, [], units, "c"))


def test_parity_main_passes_with_equal_providers(monkeypatch, capsys):
    _parity_setup(monkeypatch)
    monkeypatch.setattr(parity, "_embedders", lambda s: (FakeEmbedder(dim=s.embedding_dim),) * 2)
    assert parity.main() == 0
    out = capsys.readouterr().out
    assert "Coseno medio: 1.0000" in out and "Top-5 iguales: 20/20" in out
    assert "Resultado: PASA" in out


def test_parity_main_unreachable_provider(monkeypatch, capsys):
    _parity_setup(monkeypatch)

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    def embedders(s):
        return tuple(
            OllamaEmbedder(
                name=s.embedding_model,
                dim=s.embedding_dim,
                normalize=True,
                batch_size=8,
                base_url=s.parity_ollama_base_url,
                provider_model="bge-m3",
                transport=httpx.MockTransport(refuse),
            )
            for _ in range(2)
        )

    monkeypatch.setattr(parity, "_embedders", embedders)
    assert parity.main() == 2
    err = capsys.readouterr().err
    assert "no se pudo conectar con Ollama" in err


def test_parity_main_needs_production_url(monkeypatch, capsys):
    _parity_setup(monkeypatch, parity_prod_base_url="")
    assert parity.main() == 2
    assert "PARITY_PROD_BASE_URL" in capsys.readouterr().err


def test_config_problems_for_readyz():
    from app.embeddings import config_problems

    assert config_problems(Settings(embedding_provider="ollama")) == []
    assert config_problems(Settings(embedding_provider="fake", env="local")) == []
    assert config_problems(Settings(embedding_provider="fake", env="prod")) == [
        "EMBEDDING_PROVIDER=fake en producción"
    ]
    assert "desconocido" in config_problems(Settings(embedding_provider="nope"))[0]
    remote = {"embedding_provider": "openai_compat", "embedding_base_url": "https://x.org/v1"}
    assert config_problems(Settings(**remote, embedding_api_key="")) == ["falta EMBEDDING_API_KEY"]
    assert config_problems(Settings(**remote, embedding_api_key="k")) == []
    no_url = Settings(embedding_provider="openai_compat", embedding_base_url="")
    assert config_problems(no_url) == ["falta EMBEDDING_BASE_URL o EMBEDDING_PROVIDER_MODEL"]
