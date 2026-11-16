import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import api, retrieval
from app.citations import Catalogue
from app.llm import FakeLLM, LLMUnavailable
from app.models import Chunk
from app.probes import ProbeResult
from app.retrieval import IndexCheck, IndexNotReadyError
from app.settings import Settings
from tests.test_citations import EDITIONS, WORKS
from tests.test_generation import HITS, model_json

CATALOGUE = Catalogue(WORKS, EDITIONS)


def api_settings(**overrides) -> Settings:
    values = {
        "env": "local",
        "embedding_provider": "ollama",
        "active_index_version": "",
        "llm_provider": "fake",
        "retrieval_k": 5,
        "question_max_chars": 500,
        "search_max_k": 10,
        "passages_max_context": 3,
        "abstain_min_similarity": None,
        "context_max_tokens": 2000,
        "output_max_tokens": 700,
    }
    values.update(overrides)
    return Settings(**values)


class FakeConn:
    closed = False

    def close(self):
        self.closed = True


class FakeIndex:
    """In-memory stand-in for the index functions (api.IndexAccess)."""

    def __init__(self, hits=HITS, version="v1", check=None):
        self.hits = hits
        self._version = version
        self._check = check or IndexCheck(
            ok=True,
            index_version=version,
            embedding_model="BAAI/bge-m3",
            embedding_provider="ollama",
            embedding_dim=1024,
            chunk_count=3,
        )
        self.calls = []
        chunks = [h.chunk for h in hits]
        self.by_id = {c.chunk_id: c for c in chunks}

    def access(self) -> api.IndexAccess:
        return api.IndexAccess(
            retrieve=self.retrieve,
            get_chunk=lambda chunk_id, conn=None: self.by_id.get(chunk_id),
            neighbours=self.neighbours,
            version=self.version,
            check=lambda conn, settings: self._check,
        )

    def version(self, conn=None):
        if self._version is None:
            raise IndexNotReadyError("No hay índice activo")
        return self._version

    def retrieve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.hits[: kwargs["k"]]

    def neighbours(self, chunk_id, conn=None, window=1) -> list[Chunk]:
        seq = self.by_id[chunk_id].seq
        return [
            c for c in self.by_id.values() if c.chunk_id != chunk_id and abs(c.seq - seq) <= window
        ]


def no_probe():
    """Live provider checks replaced: they would call the real providers."""
    return lambda settings: ProbeResult()


def make_client(*, settings=None, index=None, llm=None, connector=None) -> TestClient:
    app = api.create_app(settings or api_settings())
    index = index or FakeIndex()

    def no_conn():
        yield None

    app.dependency_overrides.update(
        {
            api.get_app_settings: lambda: settings or api_settings(),
            api.get_catalogue: lambda: CATALOGUE,
            api.get_index_access: index.access,
            api.get_conn: no_conn,
            api.get_llm_client: lambda: llm if llm is not None else FakeLLM([model_json()]),
            api.get_connector: lambda: connector or (lambda s: FakeConn()),
            api.get_probe: no_probe,
        }
    )
    client = TestClient(app)
    client.index = index
    return client


# --- health and catalogue ----------------------------------------------------------------


def test_healthz():
    assert make_client().get("/healthz").json() == {"status": "ok"}


def test_works_lists_catalogue_with_languages():
    works = {w["work_id"]: w for w in make_client().get("/works").json()}
    assert set(works) == set(WORKS)
    ne = works["eth-nic"]
    assert ne["languages"] == ["en", "es"]
    assert ne["title_es"] == "Ética a Nicómaco"
    assert {e["edition_id"] for e in ne["editions"]} == {"eth-nic.en.test", "eth-nic.es.azcarate"}
    assert ne["editions"][0]["licence"] == "CC BY-SA 4.0"
    assert works["oec"]["authenticity"] == "disputed"


# --- /search ----------------------------------------------------------------------------


def test_search_returns_hits_with_citations():
    client = make_client()
    body = client.post(
        "/search", json={"query": "¿Qué es la virtud?", "k": 2, "works": ["eth-nic"]}
    ).json()
    assert body["index_version"] == "v1"
    assert body["query_lang"] == "es"
    assert [r["rank"] for r in body["results"]] == [1, 2]
    citation = body["results"][0]["citation"]
    assert citation["chunk_id"] == HITS[0].chunk.chunk_id
    assert citation["locator"] == "EN II.1, 1101a1–20 (aprox.)"
    assert citation["translator"] == "Test Translator"
    assert citation["authenticity"] == "genuine"
    _, kwargs = client.index.calls[0]
    assert kwargs["k"] == 2 and kwargs["works"] == ["eth-nic"]
    assert kwargs["include_dubious"] is True and kwargs["lang"] is None


def test_search_default_k_comes_from_settings():
    client = make_client(settings=api_settings(retrieval_k=3))
    client.post("/search", json={"query": "virtud"})
    assert client.index.calls[0][1]["k"] == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "virtud", "k": 11},
        {"query": "virtud", "k": 0},
        {"query": ""},
        {"query": "   "},
        {"query": "virtud", "works": ["nope"]},
        {"query": "virtud", "lang": "grc"},
        {"query": "virtud", "unknown_field": 1},
        {"query": "x" * 501},
    ],
)
def test_search_validation(payload):
    client = make_client()
    assert client.post("/search", json=payload).status_code == 422
    assert client.index.calls == []


# --- /ask -------------------------------------------------------------------------------


def test_ask_happy_path():
    llm = FakeLLM([model_json(text="Es un término medio [1].", citations=[1])])
    client = make_client(llm=llm)
    response = client.post(
        "/ask", json={"question": "¿Qué es la virtud?", "lang": "en", "include_dubious": False}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Es un término medio [1]."
    assert body["answer_lang"] == "es"
    assert body["abstained"] is False and body["search_only"] is False
    assert body["error"] is None
    assert [c["n"] for c in body["citations"]] == [1]
    citation = body["citations"][0]
    for field in ("work_title", "locator", "translator", "text", "licence", "authenticity"):
        assert citation[field]
    assert [r["chunk_id"] for r in body["retrieved"]] == [h.chunk.chunk_id for h in HITS]
    assert body["index_version"] == "v1"
    assert len(body["prompt_version"]) == 8
    assert body["model"] == "fake/scripted-llm"
    assert set(body["latency_ms"]) == {"retrieve", "llm", "total"}
    assert "embed_text" not in response.text  # internal fields are not exposed
    _, kwargs = client.index.calls[0]
    assert kwargs["lang"] == "en" and kwargs["include_dubious"] is False


def test_ask_search_only_exposes_an_error_code_not_the_provider_message():
    llm = FakeLLM([LLMUnavailable("groq: HTTP 429 https://api.groq.com secret detail")])
    body = make_client(llm=llm).post("/ask", json={"question": "¿Qué es la virtud?"}).json()
    assert body["search_only"] is True and body["abstained"] is False
    assert body["error"] == "llm_unavailable"
    assert "groq" not in json.dumps(body["error"])
    assert len(body["citations"]) == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "x" * 501},
        {"question": ""},
        {"question": "virtud", "works": ["eth-nic", "nope"]},
        {"question": "virtud", "k": 3},  # k is fixed on the server for /ask
        {"question": "virtud", "lang": "fr"},
        {},
    ],
)
def test_ask_validation(payload):
    client = make_client(llm=FakeLLM([]))
    assert client.post("/ask", json=payload).status_code == 422
    assert client.index.calls == []


def test_ask_question_limit_comes_from_settings():
    client = make_client(settings=api_settings(question_max_chars=10), llm=FakeLLM([]))
    response = client.post("/ask", json={"question": "una pregunta larga"})
    assert response.status_code == 422
    assert "10" in response.json()["detail"][0]["msg"]


def test_ask_without_index_is_503():
    client = make_client(index=FakeIndex(version=None), llm=FakeLLM([]))
    response = client.post("/ask", json={"question": "¿Qué es la virtud?"})
    assert response.status_code == 503
    assert response.json() == {"detail": "El índice no está listo."}


# --- /passages ----------------------------------------------------------------------------


def test_passage_with_neighbours():
    client = make_client()
    chunk_id = HITS[1].chunk.chunk_id  # seq 2, between 1 and 3
    response = client.get(f"/passages/{chunk_id.replace('#', '%23')}", params={"context": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["passage"]["chunk_id"] == chunk_id
    assert body["passage"]["locator"] == "EN II.2, 1102a1–20 (aprox.)"
    assert body["passage"]["divisions"] == [["book", "2"], ["chapter", "2"]]
    assert [p["chunk_id"] for p in body["before"]] == [HITS[0].chunk.chunk_id]
    assert [p["chunk_id"] for p in body["after"]] == [HITS[2].chunk.chunk_id]

    alone = client.get(f"/passages/{chunk_id.replace('#', '%23')}", params={"context": 0})
    assert alone.json()["before"] == [] and alone.json()["after"] == []
    english = client.get(f"/passages/{chunk_id.replace('#', '%23')}", params={"lang": "en"})
    assert english.json()["passage"]["work_title"] == "Nicomachean Ethics"


def test_passage_not_found_and_context_limit():
    client = make_client()
    assert client.get("/passages/nope%231").status_code == 404
    chunk_id = HITS[0].chunk.chunk_id.replace("#", "%23")
    assert client.get(f"/passages/{chunk_id}", params={"context": 4}).status_code == 422
    assert client.get(f"/passages/{chunk_id}", params={"context": -1}).status_code == 422


# --- /readyz ------------------------------------------------------------------------------


def test_readyz_ok():
    response = make_client().get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True and body["database"] is True
    assert body["index"]["index_version"] == "v1"
    assert body["llm"]["provider"] == "fake"
    assert body["problems"] == []


def test_readyz_without_database_is_503():
    def refuse(settings):
        raise psycopg.OperationalError("connection refused")

    response = make_client(connector=refuse).get("/readyz")
    assert response.status_code == 503
    assert response.json()["problems"] == ["base de datos no disponible"]


def test_readyz_without_index_is_503_with_reasons():
    check = IndexCheck(ok=False, problems=["No hay índice activo: correr `make index`."])
    response = make_client(index=FakeIndex(check=check)).get("/readyz")
    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert "No hay índice activo" in response.json()["problems"][0]


def test_readyz_without_embedding_config_is_503():
    s = api_settings(
        embedding_provider="openai_compat",
        embedding_base_url="https://api.example.org/v1",
        embedding_api_key="",
    )
    response = make_client(settings=s).get("/readyz")
    assert response.status_code == 503
    assert response.json()["problems"] == ["falta EMBEDDING_API_KEY"]
    assert response.json()["embedding"]["configured"] is False


def test_readyz_without_llm_config_is_503():
    s = api_settings(llm_provider="openai_compat", llm_api_key="")
    response = make_client(settings=s).get("/readyz")
    assert response.status_code == 503
    assert response.json()["problems"] == ["falta LLM_API_KEY"]
    assert response.json()["llm"]["configured"] is False


# --- against Postgres ---------------------------------------------------------------------


def schema_connector(schema: str):
    from pgvector.psycopg import register_vector

    from tests.test_db_support import db_url

    def connect(settings):
        conn = psycopg.connect(
            db_url(),
            options=f"-c search_path={schema},public",
            autocommit=True,
        )
        register_vector(conn)
        return conn

    return connect


@pytest.mark.db
def test_readyz_and_ask_on_a_real_database(monkeypatch):
    from app.index import build_index
    from tests.test_db_support import EDITIONS as DB_EDITIONS
    from tests.test_db_support import WORKS as DB_WORKS
    from tests.test_db_support import (
        fake_embedder,
        fake_settings,
        synthetic_units,
        temp_schema_connection,
    )

    monkeypatch.setattr(retrieval, "_catalogue", lambda: DB_WORKS)
    monkeypatch.setattr(retrieval, "_default_embedder", fake_embedder)
    settings = fake_settings(llm_provider="fake")
    with temp_schema_connection() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        app = api.create_app(settings)
        app.dependency_overrides.update(
            {
                api.get_app_settings: lambda: settings,
                api.get_catalogue: lambda: Catalogue(
                    DB_WORKS, {e.edition_id: e for e in DB_EDITIONS}
                ),
                api.get_connector: lambda: schema_connector(schema),
                api.get_llm_client: lambda: FakeLLM([model_json(text="Tres [1].")]),
                api.get_probe: no_probe,
            }
        )
        client = TestClient(app)

        not_ready = client.get("/readyz")
        assert not_ready.status_code == 503
        assert "No hay índice activo" in not_ready.json()["problems"][0]
        assert client.post("/ask", json={"question": "¿Qué es la amistad?"}).status_code == 503

        built = build_index(
            synthetic_units(),
            works=DB_WORKS,
            editions=DB_EDITIONS,
            conn=conn,
            embedder=fake_embedder(),
            settings=settings,
            log=lambda _: None,
        )
        ready = client.get("/readyz")
        assert ready.status_code == 200, ready.json()
        assert ready.json()["index"]["index_version"] == built.index_version

        body = client.post("/ask", json={"question": "¿Cuáles son las clases de amistad?"}).json()
        assert body["index_version"] == built.index_version
        assert body["citations"][0]["chunk_id"] == "eth-nic.es.test:8.3#1"
        assert body["citations"][0]["chunk_id"] in {r["chunk_id"] for r in body["retrieved"]}

        chunk_id = body["citations"][0]["chunk_id"].replace("#", "%23")
        passage = client.get(f"/passages/{chunk_id}").json()
        assert passage["passage"]["text"].startswith("La amistad")
