"""Live provider checks for /readyz (app.probes), with stub providers: no network."""

import httpx

from app.embeddings import EmbeddingError
from app.probes import ProviderProbe, probe_embeddings, probe_llm
from app.settings import Settings


def settings(**overrides) -> Settings:
    values = {
        "embedding_provider": "openai_compat",
        "embedding_base_url": "https://embed.example.org/v1",
        "embedding_api_key": "ek",
        "embedding_dim": 4,
        "llm_provider": "openai_compat",
        "llm_base_url": "https://llm.example.org/v1",
        "llm_model": "openai/gpt-oss-20b",
        "llm_api_key": "sk-SECRET",
        "readyz_probe_ttl_s": 300.0,
    }
    values.update(overrides)
    return Settings(**values)


class StubEmbedder:
    def __init__(self, dim=4, error=None):
        self.dim, self.error, self.calls = dim, error, 0

    def embed_query(self, text):
        self.calls += 1
        if self.error:
            raise self.error
        return [0.5] * self.dim


def models_transport(status=200, ids=("openai/gpt-oss-20b",), seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={"data": [{"id": i} for i in ids]})

    return httpx.MockTransport(handler)


def test_embedding_probe():
    assert probe_embeddings(settings(), lambda s: StubEmbedder()).problems == []
    wrong = probe_embeddings(settings(), lambda s: StubEmbedder(dim=3)).problems
    assert wrong and "3 dimensiones" in wrong[0]
    down = probe_embeddings(settings(), lambda s: StubEmbedder(error=EmbeddingError("x")))
    assert down.problems == ["embeddings: el proveedor no respondió a la prueba"]
    fake = probe_embeddings(settings(embedding_provider="fake"), lambda s: 1 / 0)
    assert fake.problems == []  # not probed


def test_embedding_probe_does_not_retry_or_wait_long():
    used = []

    def factory(s):
        used.append(s)
        return StubEmbedder()

    probe_embeddings(settings(readyz_probe_timeout_s=3.0), factory)
    assert used[0].embedding_max_retries == 0 and used[0].embedding_timeout_s == 3.0


def test_llm_probe_lists_the_models():
    seen = []
    assert probe_llm(settings(), models_transport(seen=seen)).problems == []
    assert str(seen[0].url) == "https://llm.example.org/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-SECRET"
    retired = probe_llm(settings(), models_transport(ids=("other",))).problems
    assert retired == ["LLM: el proveedor no ofrece el modelo openai/gpt-oss-20b"]
    rejected = probe_llm(settings(), models_transport(status=401)).problems
    assert rejected == ["LLM: el proveedor rechazó la prueba (HTTP 401)"]
    unlisted = probe_llm(settings(), models_transport(status=404))
    assert unlisted.problems == [] and "no lista modelos" in unlisted.warnings[0]

    def refuse(request):
        raise httpx.ConnectError("refused")

    down = probe_llm(settings(), httpx.MockTransport(refuse)).problems
    assert down == ["LLM: el proveedor no respondió a la prueba"]
    assert probe_llm(settings(llm_provider="fake"), httpx.MockTransport(refuse)).problems == []


def test_probe_results_are_cached():
    clock = [0.0]
    embedder = StubEmbedder()
    seen = []
    probe = ProviderProbe(lambda: clock[0], lambda s: embedder, models_transport(seen=seen))
    s = settings()
    assert probe(s).problems == [] and probe(s).problems == []
    assert embedder.calls == 1 and len(seen) == 1  # the second call used the cache
    clock[0] += 301
    probe(s)
    assert embedder.calls == 2 and len(seen) == 2
    probe(settings(llm_model="other"))  # another configuration is probed again
    assert len(seen) == 3


def test_no_secret_in_probe_messages():
    def leak(request):
        return httpx.Response(500, text="bad key sk-SECRET")

    result = probe_llm(settings(), httpx.MockTransport(leak))
    assert "SECRET" not in " ".join(result.problems + result.warnings)
