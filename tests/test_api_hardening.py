"""Etapa 7 hardening of the API: per-IP limits, eval key, daily token budget, answer
cache, per-query JSON log, CORS and secrets. Unit tests use in-memory stand-ins for the
Postgres parts; the tests marked `db` run the same flows against Postgres."""

import io
import json
import logging
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import api, limits, retrieval
from app.citations import Catalogue
from app.db import MIGRATIONS_DIR, migrate
from app.llm import FakeLLM, LLMResponse, LLMUnavailable
from app.logging_setup import QUERY_LOGGER, JsonFormatter, log_query, setup_logging
from tests.test_api import (
    CATALOGUE,
    FakeConn,
    FakeIndex,
    api_settings,
    no_probe,
    schema_connector,
)
from tests.test_db_support import db_url
from tests.test_generation import HITS, model_json
from tests.test_limits import MemoryUsage

QUESTION = "¿Qué es la virtud?"


def today():
    return datetime.now(UTC).date()


class MemoryCache:
    """In-memory stand-in for app.cache.AnswerCache (same methods)."""

    def __init__(self):
        self.entries: dict[str, dict] = {}
        self.gets = 0
        self.puts = 0

    def get(self, key):
        self.gets += 1
        return self.entries.get(key.key)

    def put(self, key, response):
        self.puts += 1
        self.entries[key.key] = response
        return True


def answers_llm(n: int = 20) -> FakeLLM:
    return FakeLLM([model_json(text="Es un término medio [1].")] * n)


def make_client(
    *,
    settings=None,
    index=None,
    llm=None,
    usage=None,
    answers=None,
    connector=None,
) -> TestClient:
    s = settings or api_settings()
    app = api.create_app(s)
    index = index or FakeIndex()
    llm = llm if llm is not None else answers_llm()

    def no_conn():
        yield None

    app.dependency_overrides.update(
        {
            api.get_app_settings: lambda: s,
            api.get_catalogue: lambda: CATALOGUE,
            api.get_index_access: index.access,
            api.get_conn: no_conn,
            api.get_llm_client: lambda: llm,
            api.get_connector: lambda: connector or (lambda _: FakeConn()),
            api.get_probe: no_probe,
            api.get_usage: lambda: usage,
            api.get_answer_cache: lambda: answers,
        }
    )
    client = TestClient(app)
    client.index, client.llm = index, llm
    return client


def ask(client, question=QUESTION, headers=None, **body):
    return client.post("/ask", json={"question": question, **body}, headers=headers or {})


# --- per-IP limits ---------------------------------------------------------------------------


def test_sixth_ask_in_a_minute_from_one_ip_is_429():
    client = make_client()
    for i in range(5):
        response = ask(client, f"{QUESTION} {i}")
        assert response.status_code == 200, response.json()
    refused = ask(client, "¿Y la justicia?")
    assert refused.status_code == 429
    body = refused.json()
    assert body["limit"] == "minute"
    assert "5 preguntas por minuto" in body["detail"]
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert body["retry_after"] == int(refused.headers["Retry-After"])
    assert refused.headers["X-Request-ID"]
    assert len(client.llm.calls) == 5  # the refused question never reached the LLM
    assert len(client.index.calls) == 5


def test_clients_behind_the_trusted_proxy_are_counted_apart():
    # TestClient connects as "testclient": trusting it makes it the proxy.
    s = api_settings(forwarded_allow_ips="testclient", rate_limit_per_minute=2)
    client = make_client(settings=s)
    alice = {"X-Forwarded-For": "198.51.100.1"}
    assert [ask(client, headers=alice).status_code for _ in range(3)] == [200, 200, 429]
    assert ask(client, headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200
    # Alice cannot get around the limit by forging an address in front of hers.
    forged = {"X-Forwarded-For": "6.6.6.6, 198.51.100.1"}
    assert ask(client, headers=forged).status_code == 429


def test_one_ipv6_network_counts_as_one_client():
    s = api_settings(forwarded_allow_ips="testclient", rate_limit_per_minute=2)
    client = make_client(settings=s)
    codes = [
        ask(client, headers={"X-Forwarded-For": f"2001:db8:1:2::{i:x}"}).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]
    assert ask(client, headers={"X-Forwarded-For": "2001:db8:1:3::1"}).status_code == 200


def test_forwarded_for_from_an_untrusted_peer_is_ignored():
    client = make_client(settings=api_settings(rate_limit_per_minute=2))
    codes = [
        ask(client, headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_daily_limit_uses_the_persistent_counter():
    usage = MemoryUsage()
    client = make_client(settings=api_settings(rate_limit_per_day=2), usage=usage)
    assert [ask(client).status_code for _ in range(2)] == [200, 200]
    refused = ask(client)
    assert refused.status_code == 429
    assert refused.json()["limit"] == "day"
    assert "2 preguntas por día" in refused.json()["detail"]
    assert 1 <= int(refused.headers["Retry-After"]) <= 86_400
    assert sum(usage.requests.values()) == 2


def test_eval_key_skips_the_limits_the_budget_and_the_cache():
    usage, answers = MemoryUsage(), MemoryCache()
    usage.add_tokens(today(), limits.PUBLIC, 10**9)  # public budget spent
    s = api_settings(eval_key="ek-123", rate_limit_per_minute=1, rate_limit_per_day=1)
    client = make_client(settings=s, usage=usage, answers=answers)
    for _ in range(3):
        body = ask(client, headers={"X-Eval-Key": "ek-123"}).json()
        assert body["search_only"] is False and body["cached"] is False
    assert usage.requests == {}  # not counted against any IP
    assert usage.tokens_used(today(), limits.EVAL) > 0
    assert answers.gets == 0 and answers.puts == 0
    wrong = {"X-Eval-Key": "nope"}
    assert ask(client, headers=wrong).status_code == 200  # an ordinary request...
    assert ask(client, headers=wrong).status_code == 429  # ...with the ordinary limits


# --- daily token budget -----------------------------------------------------------------------


def test_spent_budget_gives_search_only_without_calling_the_llm():
    usage = MemoryUsage()
    usage.add_tokens(today(), limits.PUBLIC, 1000)
    llm = FakeLLM([])  # any call would fail the request
    client = make_client(settings=api_settings(daily_token_budget=1000), usage=usage, llm=llm)
    response = ask(client)
    assert response.status_code == 200
    body = response.json()
    assert body["search_only"] is True and body["abstained"] is False
    assert body["error"] == "budget_exhausted"
    assert len(body["citations"]) == len(HITS)
    assert llm.calls == []


def test_tokens_of_each_answer_are_added_to_the_budget():
    usage = MemoryUsage()
    client = make_client(usage=usage)
    body = ask(client).json()
    spent = body["tokens_in"] + body["tokens_out"]
    assert spent > 0
    assert usage.tokens_used(today()) == spent
    ask(client, "¿Y la justicia?")
    assert usage.tokens_used(today()) > spent


def test_tokens_are_reserved_before_the_llm_is_called():
    # While one request waits for the LLM its estimate is already counted, so a request
    # arriving meanwhile cannot also pass a budget that holds one answer.
    usage = MemoryUsage()
    s = api_settings(daily_token_budget=limits.answer_token_estimate(api_settings()))
    seen = []

    def answer_and_race(messages):
        seen.append(usage.tokens_used(today()))
        concurrent = ask(client, "¿Y la justicia?").json()
        seen.append(concurrent["error"])
        return model_json()

    client = make_client(settings=s, usage=usage, llm=FakeLLM([answer_and_race]))
    body = ask(client).json()
    assert seen == [limits.answer_token_estimate(s), "budget_exhausted"]
    assert body["search_only"] is False
    assert usage.tokens_used(today()) == body["tokens_in"] + body["tokens_out"]  # settled


def test_llm_failure_gives_search_only_200():
    """Plan, Etapa 7: with the LLM failing, /ask answers 200 in search-only mode."""
    llm = FakeLLM([LLMUnavailable("groq: HTTP 429", status_code=429)])
    response = ask(make_client(llm=llm))
    assert response.status_code == 200
    assert response.json()["search_only"] is True
    assert response.json()["error"] == "llm_unavailable"


# --- answer cache -----------------------------------------------------------------------------


def test_repeated_question_is_served_from_the_cache():
    answers = MemoryCache()
    client = make_client(answers=answers)
    first = ask(client).json()
    assert first["cached"] is False and answers.puts == 1
    again = ask(client, "  ¿Qué es la   virtud? ").json()
    assert again["cached"] is True
    assert again["answer"] == first["answer"]
    assert again["citations"] == first["citations"]
    assert again["retrieved"] == first["retrieved"]
    assert again["tokens_in"] == 0 and again["tokens_out"] == 0
    assert set(again["latency_ms"]) == {"cache", "total"}
    assert len(client.llm.calls) == 1 and len(client.index.calls) == 1
    # Other filters are another question.
    assert ask(client, works=["eth-nic"]).json()["cached"] is False
    assert len(client.llm.calls) == 2


def test_search_only_and_fallback_answers_are_not_cached():
    answers = MemoryCache()
    fallback = LLMResponse(
        text=model_json(),
        model="@cf/openai/gpt-oss-20b",
        provider="workers-ai",
        tokens_in=10,
        tokens_out=5,
    )
    llm = FakeLLM([LLMUnavailable("down"), fallback])
    client = make_client(answers=answers, llm=llm)
    assert ask(client).json()["search_only"] is True
    assert ask(client).json()["model"] == "@cf/openai/gpt-oss-20b"
    assert answers.puts == 0 and answers.entries == {}


def test_cache_entry_with_an_old_schema_is_regenerated():
    answers = MemoryCache()
    client = make_client(answers=answers)
    ask(client)
    (key,) = answers.entries
    answers.entries[key] = {"answer": "sin los demás campos"}
    body = ask(client).json()
    assert body["cached"] is False and len(client.llm.calls) == 2
    assert answers.entries[key]["answer"] == body["answer"]


# --- per-query log ----------------------------------------------------------------------------


def query_events(caplog) -> list[dict]:
    return [r.event for r in caplog.records if r.name == QUERY_LOGGER]


def test_one_json_log_line_per_ask(caplog):
    answers = MemoryCache()
    client = make_client(answers=answers)
    with caplog.at_level(logging.INFO, logger=QUERY_LOGGER):
        response = ask(client, works=["eth-nic"], lang="en")
    (event,) = query_events(caplog)
    assert event["event"] == "query" and event["endpoint"] == "/ask"
    assert event["request_id"] == response.headers["X-Request-ID"]
    assert event["status"] == 200 and event["lang"] == "es"
    assert event["filters"] == {"works": ["eth-nic"], "lang": "en", "include_dubious": True}
    assert [r["chunk_id"] for r in event["retrieved"]] == [h.chunk.chunk_id for h in HITS]
    assert all(isinstance(r["score"], float) for r in event["retrieved"])
    assert event["citations"] == [HITS[0].chunk.chunk_id]
    assert event["index_version"] == "v1" and len(event["prompt_version"]) == 8
    assert event["model"] == "fake/scripted-llm"
    assert event["tokens_in"] > 0 and event["tokens_out"] > 0
    assert {"limits", "cache", "retrieve", "llm", "total", "request"} <= set(event["latency_ms"])
    assert event["abstained"] is False and event["search_only"] is False
    assert event["error"] is None and event["cache"] == "stored" and event["eval"] is False
    assert len(event["question_sha256"]) == 16 and len(event["client"]) == 12
    record = next(r for r in caplog.records if r.name == QUERY_LOGGER)
    line = JsonFormatter().format(record)
    assert "virtud" not in line and "testclient" not in line  # hashes, not text or address
    assert json.loads(line)["request_id"] == event["request_id"]


def test_log_lines_for_cache_hits_errors_and_429(caplog):
    client = make_client(
        settings=api_settings(rate_limit_per_minute=2),
        answers=MemoryCache(),
        llm=FakeLLM([model_json(), LLMUnavailable("groq: HTTP 503 detail")]),
    )
    with caplog.at_level(logging.INFO, logger=QUERY_LOGGER):
        ask(client)
        ask(client)  # cache hit
        ask(client, "¿Y la justicia?")  # 429
    first, hit, refused = query_events(caplog)
    assert first["cache"] == "stored"
    assert hit["cache"] == "hit" and hit["tokens_in"] == 0
    assert refused["status"] == 429 and refused["rate_limited"] == "minute"

    caplog.clear()
    client = make_client(llm=FakeLLM([LLMUnavailable("groq: HTTP 503 detail")]))
    with caplog.at_level(logging.INFO, logger=QUERY_LOGGER):
        ask(client)
    (failed,) = query_events(caplog)
    assert failed["search_only"] is True
    assert failed["error"].startswith("llm_unavailable: groq: HTTP 503")  # full, in the log


def test_search_is_logged_too(caplog):
    client = make_client()
    with caplog.at_level(logging.INFO, logger=QUERY_LOGGER):
        response = client.post("/search", json={"query": "virtud", "k": 2})
    (event,) = query_events(caplog)
    assert event["endpoint"] == "/search"
    assert event["request_id"] == response.headers["X-Request-ID"]
    assert len(event["retrieved"]) == 2 and event["filters"]["k"] == 2
    assert "question" not in event


def test_question_text_is_logged_only_when_asked(caplog):
    client = make_client(settings=api_settings(log_question_text=True))
    with caplog.at_level(logging.INFO, logger=QUERY_LOGGER):
        ask(client)
    (event,) = query_events(caplog)
    assert event["question"] == QUESTION


@contextmanager
def restored_root_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        yield root
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def test_setup_logging_writes_json_lines_once():
    stream = io.StringIO()
    with restored_root_logging() as root:
        setup_logging("INFO", stream=stream)
        setup_logging("INFO", stream=stream)
        assert sum(h.get_name() == "arag-json" for h in root.handlers) == 1
        log_query({"request_id": "r1", "tokens_in": 3, "latency_ms": {"llm": 1.5}})
        logging.getLogger("app.test").warning("algo pasó: %s", "detalle")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    query, other = (json.loads(line) for line in lines)
    assert query["event"] == "query" and query["request_id"] == "r1"
    assert query["latency_ms"] == {"llm": 1.5} and query["logger"] == QUERY_LOGGER
    assert other["msg"] == "algo pasó: detalle" and other["level"] == "WARNING"


def test_serving_the_app_sets_up_json_logging_and_warns_about_config(caplog):
    s = api_settings(env="prod", llm_provider="openai_compat", llm_api_key="k")
    app = api.create_app(s, startup_check=None)
    with (
        restored_root_logging() as root,
        caplog.at_level(logging.WARNING, logger="app.api"),
        TestClient(app),
    ):
        assert any(h.get_name() == "arag-json" for h in root.handlers)
    assert "FORWARDED_ALLOW_IPS" in caplog.text


# --- CORS, readiness and secrets --------------------------------------------------------------


def test_cors_allows_only_the_configured_origins():
    s = api_settings(cors_origins=["https://a-rag.example.org"])
    client = make_client(settings=s)
    preflight = {"Access-Control-Request-Method": "POST"}
    ok = client.options("/ask", headers={"Origin": "https://a-rag.example.org", **preflight})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "https://a-rag.example.org"
    other = client.options("/ask", headers={"Origin": "https://evil.example", **preflight})
    assert other.status_code == 400
    assert "access-control-allow-origin" not in other.headers
    simple = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers
    allowed = ask(client, headers={"Origin": "https://a-rag.example.org"})
    assert "x-request-id" in allowed.headers["access-control-expose-headers"].lower()


def test_readyz_warns_about_production_settings():
    s = api_settings(env="prod", llm_provider="openai_compat", llm_api_key="k")
    response = make_client(settings=s).get("/readyz")
    assert response.status_code == 200  # warnings do not make it unready
    warnings = " ".join(response.json()["warnings"])
    assert "FORWARDED_ALLOW_IPS" in warnings and "EVAL_KEY" in warnings


SECRETS = {
    "llm_api_key": "sk-llm-SECRET-1",
    "llm_fallback_api_key": "sk-fallback-SECRET-2",
    "embedding_api_key": "sk-embed-SECRET-3",
    "judge_api_key": "sk-judge-SECRET-4",
    "eval_key": "ek-SECRET-5",
    "database_url": "postgresql://app:pw-SECRET-6@db.example.org:5432/arag",
}


def test_no_secret_reaches_a_response():
    s = api_settings(**SECRETS, llm_provider="openai_compat", rate_limit_per_minute=3)
    leak = f"provider said: bad key {SECRETS['llm_api_key']} at {SECRETS['database_url']}"
    llm = FakeLLM([LLMUnavailable(leak), model_json(), model_json()])
    client = make_client(settings=s, llm=llm)

    def refuse(_):
        raise psycopg.OperationalError(f"cannot reach {SECRETS['database_url']}")

    responses = [
        client.get("/healthz"),
        client.get("/readyz"),
        client.get("/works"),
        client.get("/openapi.json"),
        client.post("/search", json={"query": "virtud"}),
        ask(client),  # search-only, the provider's message must not leak
        ask(client, "¿Y la justicia?"),
        ask(client, "¿Y la amistad?"),
        ask(client, "¿Y el placer?"),  # 429
        make_client(settings=s, connector=refuse).get("/readyz"),
    ]
    assert responses[5].json()["error"] == "llm_unavailable"
    assert responses[8].status_code == 429
    for response in responses:
        dump = response.text + json.dumps(dict(response.headers))
        for secret in ("SECRET", "pw-"):
            assert secret not in dump, (response.request.url, dump[:300])


# --- against Postgres -------------------------------------------------------------------------


@contextmanager
def committed_schema(migrations_dir):
    """A schema with the migrations of `migrations_dir` applied and committed, visible to
    the API's own connections; dropped afterwards. Skips when Postgres is unreachable."""
    try:
        conn = psycopg.connect(db_url(), connect_timeout=3, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres no disponible en DATABASE_URL: {exc}")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    try:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}, public")
        migrate(conn, migrations_dir)
        yield schema
    finally:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.close()


def db_client(schema, settings, catalogue, llm_factory) -> TestClient:
    app = api.create_app(settings)
    app.dependency_overrides.update(
        {
            api.get_app_settings: lambda: settings,
            api.get_catalogue: lambda: catalogue,
            api.get_connector: lambda: schema_connector(schema),
            api.get_llm_client: llm_factory,
            api.get_probe: no_probe,
        }
    )
    return TestClient(app)


@pytest.mark.db
def test_limits_budget_and_cache_in_postgres(monkeypatch):
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
    catalogue = Catalogue(DB_WORKS, {e.edition_id: e for e in DB_EDITIONS})
    settings = fake_settings(
        llm_provider="fake",
        rate_limit_per_minute=100,
        rate_limit_per_day=4,
        eval_key="ek-db",
        daily_token_budget=100_000,
    )

    def llm_factory():
        return FakeLLM([model_json(text="Tres clases [1].")])

    with temp_schema_connection() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        build_index(
            synthetic_units(),
            works=DB_WORKS,
            editions=DB_EDITIONS,
            conn=conn,
            embedder=fake_embedder(),
            settings=settings,
            log=lambda _: None,
        )
        client = db_client(schema, settings, catalogue, llm_factory)
        ready = client.get("/readyz")
        assert ready.status_code == 200, ready.json()

        question = "¿Cuáles son las clases de amistad?"
        first = ask(client, question).json()
        assert first["cached"] is False and first["citations"]
        second = ask(client, f"  {question} ").json()  # same question, normalised
        assert second["cached"] is True and second["answer"] == first["answer"]

        def scalar(sql):
            with psycopg.connect(db_url(), options=f"-c search_path={schema}") as c:
                return c.execute(sql).fetchone()[0]

        assert scalar("SELECT count(*) FROM answer_cache") == 1
        spent = first["tokens_in"] + first["tokens_out"]
        assert scalar("SELECT tokens FROM token_usage_daily WHERE source = 'public'") == spent

        # The per-day count survives a restart (a new app has a new per-minute limiter).
        restarted = db_client(schema, settings, catalogue, llm_factory)
        assert ask(restarted, "¿Qué es la valentía?").status_code == 200  # 3rd today
        assert ask(restarted, "¿Qué es el placer?").status_code == 200  # 4th
        refused = ask(restarted, "¿Qué es la ciudad?")
        assert refused.status_code == 429 and refused.json()["limit"] == "day"

        cached_before = scalar("SELECT count(*) FROM answer_cache")
        assert cached_before == 3  # the three answered questions

        # Budget spent: a new public question is search-only; an eval run still gets the LLM.
        tight = settings.model_copy(update={"daily_token_budget": 1, "rate_limit_per_day": 0})
        spent_client = db_client(schema, tight, catalogue, llm_factory)
        body = ask(spent_client, "¿Qué es la justicia?").json()
        assert body["search_only"] is True and body["error"] == "budget_exhausted"
        evaluated = ask(spent_client, "¿Qué es la justicia?", headers={"X-Eval-Key": "ek-db"})
        assert evaluated.json()["search_only"] is False
        assert scalar("SELECT tokens FROM token_usage_daily WHERE source = 'eval'") > 0
        assert scalar("SELECT count(*) FROM answer_cache") == cached_before  # neither cached


@pytest.mark.db
def test_readyz_reports_missing_migration(tmp_path):
    (tmp_path / "001_init.sql").write_text((MIGRATIONS_DIR / "001_init.sql").read_text())
    settings = api_settings()
    with committed_schema(tmp_path) as schema:
        client = db_client(schema, settings, CATALOGUE, lambda: FakeLLM([]))
        response = client.get("/readyz")
        assert response.status_code == 503
        problems = " ".join(response.json()["problems"])
        assert "rate_limit_daily" in problems and "make migrate" in problems


# --- startup (rule 4) -------------------------------------------------------------------------


def test_startup_refuses_fake_models_in_production():
    s = api_settings(env="prod", llm_provider="fake", embedding_provider="fake")
    with pytest.raises(api.StartupError, match="EMBEDDING_PROVIDER y LLM_PROVIDER=fake"):
        api.check_startup(s)


def test_startup_failure_stops_the_server():
    def refuse(settings):
        raise api.StartupError("no")

    app = api.create_app(api_settings(), startup_check=refuse)
    with pytest.raises(api.StartupError), restored_root_logging(), TestClient(app):
        pass


def test_startup_without_a_database_only_logs(caplog):
    s = api_settings(database_url="postgresql://nobody@127.0.0.1:9/none")
    with caplog.at_level(logging.ERROR, logger="app.api"):
        api.check_startup(s)
    assert "sin base de datos" in caplog.text


@pytest.mark.db
def test_startup_refuses_an_index_of_another_model(monkeypatch):
    from app.index import build_index
    from tests.test_db_support import EDITIONS as DB_EDITIONS
    from tests.test_db_support import WORKS as DB_WORKS
    from tests.test_db_support import (
        fake_embedder,
        fake_settings,
        synthetic_units,
        temp_schema_connection,
    )

    with temp_schema_connection() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        build_index(
            synthetic_units(),
            works=DB_WORKS,
            editions=DB_EDITIONS,
            conn=conn,
            embedder=fake_embedder(),
            settings=fake_settings(),
            log=lambda _: None,
        )
        conn.commit()
        monkeypatch.setattr(api, "connect", lambda s, autocommit: schema_connector(schema)(s))
        api.check_startup(fake_settings())  # the configuration the index was built with
        with pytest.raises(retrieval.IndexNotReadyError, match="modelo del índice"):
            api.check_startup(api_settings())  # bge-m3 configured, fake index


# --- /search limits, request size ------------------------------------------------------------


def search(client, query="¿Qué es la virtud?", headers=None, **body):
    return client.post("/search", json={"query": query, **body}, headers=headers or {})


def test_search_has_its_own_per_ip_limits():
    usage = MemoryUsage()
    s = api_settings(
        search_rate_limit_per_minute=2,
        search_rate_limit_per_day=3,
        rate_limit_per_minute=100,
        eval_key="ek",
    )
    client = make_client(settings=s, usage=usage)
    assert [search(client).status_code for _ in range(3)] == [200, 200, 429]
    refused = search(client).json()
    assert refused["limit"] == "minute" and "búsquedas por minuto" in refused["detail"]
    assert ask(client).status_code == 200  # /ask counts apart
    assert search(client, headers={"X-Eval-Key": "ek"}).status_code == 200  # eval runs skip
    spread = api_settings(search_rate_limit_per_minute=100, search_rate_limit_per_day=3)
    client = make_client(settings=spread, usage=MemoryUsage())
    assert [search(client).status_code for _ in range(4)] == [200, 200, 200, 429]
    assert "búsquedas por día" in search(client).json()["detail"]


def test_large_bodies_are_refused_unread():
    s = api_settings(request_max_bytes=1000)
    client = make_client(settings=s)
    big = {"question": "¿Qué es la virtud?", "works": ["x" * 20] * 100}
    response = client.post("/ask", json=big)
    assert response.status_code == 413
    streamed = client.post(
        "/search",
        content=(chunk for chunk in [b'{"query": "virtud", "works": [', b'"x",' * 400, b'"y"]}']),
        headers={"Content-Type": "application/json"},
    )
    assert streamed.status_code == 413
    assert search(client).status_code == 200


def test_unknown_works_are_reported_briefly():
    client = make_client(settings=api_settings(request_max_bytes=0))
    many = search(client, works=[f"nope{i}" for i in range(500)])
    assert many.status_code == 422 and len(many.text) < 1000
    assert "Más obras que las del catálogo" in many.json()["detail"][0]["msg"]
    unknown = search(client, works=["eth-nic", "nope", "x" * 5000])
    assert unknown.status_code == 422
    detail = unknown.json()["detail"][0]["msg"]
    assert "nope" in detail and "x" * 40 in detail and "x" * 41 not in detail


# --- live readiness --------------------------------------------------------------------------


def test_readyz_reports_failing_providers():
    from app.probes import ProbeResult

    s = api_settings(llm_provider="openai_compat", llm_api_key="k")
    client = make_client(settings=s)
    client.app.dependency_overrides[api.get_probe] = lambda: (
        lambda settings: ProbeResult(problems=["LLM: el proveedor no ofrece el modelo m"])
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["problems"] == ["LLM: el proveedor no ofrece el modelo m"]
