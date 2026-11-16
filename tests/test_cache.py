import logging

import pytest

from app import cache
from app.cache import AnswerCache, make_key, normalise_question
from app.models import AskResult
from app.settings import Settings
from tests.test_db_support import temp_schema_connection

MODEL = "openai/gpt-oss-20b"


def key_for(question="¿Qué es la virtud?", settings=None, **overrides):
    values = {
        "answer_lang": "es",
        "index_version": "v1",
        "model": MODEL,
        "prompt_version": "300da7a6",
        "works": None,
        "lang": None,
        "include_dubious": True,
        "settings": settings or Settings(),
    }
    values.update(overrides)
    return make_key(question, **values)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("¿Qué es la virtud?", "Qué es la virtud"),
        ("  ¿Qué   es la\tvirtud?? ", "Qué es la virtud"),
        ("qué es la virtud", "qué es la virtud"),
        ("What is virtue?", "What is virtue"),
        ("«¿Qué dice 1106b36?»", "Qué dice 1106b36"),
        ("ﬁn último", "fin último"),  # NFKC folds the ligature
    ],
)
def test_normalise_question(raw, expected):
    assert normalise_question(raw) == expected


def test_accents_are_kept():
    assert normalise_question("¿Qué es?") != normalise_question("¿Que es?")


def test_equivalent_questions_share_a_key():
    assert key_for("¿Qué es la virtud?").key == key_for("  Qué es la   virtud ").key
    assert key_for(works=["pol", "eth-nic"]).key == key_for(works=["eth-nic", "pol"]).key


@pytest.mark.parametrize(
    "a, b",
    [
        # Retrieval reads case: a capitalised title is a soft work filter, so these two can
        # retrieve different passages and must not share a cached answer.
        ("¿Qué dice la Política sobre la valentía?", "¿qué dice la política sobre la valentía?"),
        ("¿Qué dice EN II.6?", "¿qué dice en ii.6?"),
    ],
)
def test_questions_that_differ_in_case_do_not_share_a_key(a, b):
    assert key_for(a).key != key_for(b).key


@pytest.mark.parametrize(
    "change",
    [
        {"question": "¿Qué es la justicia?"},
        {"answer_lang": "en"},
        {"index_version": "v2"},
        {"model": "other/model"},
        {"prompt_version": "deadbeef"},
        {"works": ["eth-nic"]},
        {"lang": "en"},
        {"include_dubious": False},
        {"settings": Settings(retrieval_k=8)},
        {"settings": Settings(context_max_tokens=1500)},
        {"settings": Settings(output_max_tokens=900)},
        {"settings": Settings(abstain_min_similarity=0.4)},
        {"settings": Settings(llm_reasoning_effort="medium")},
    ],
)
def test_anything_that_changes_the_answer_changes_the_key(change):
    assert key_for(**change).key != key_for().key


def test_key_keeps_only_a_hash_of_the_question():
    key = key_for("¿Qué es la virtud?")
    assert "virtud" not in repr(key)
    assert key.question_sha256 == cache.question_sha256("Qué es la virtud")
    assert key.filters == {"works": None, "lang": None, "include_dubious": True}


def result(**overrides) -> AskResult:
    values = {"answer": "Un término medio [1].", "answer_lang": "es", "abstained": False}
    values.update(overrides)
    return AskResult(**{"model": MODEL, **values})


def test_what_is_cacheable():
    assert cache.cacheable(result(), MODEL)
    assert cache.cacheable(result(abstained=True, abstain_reason="model_unanswerable"), MODEL)
    assert not cache.cacheable(result(search_only=True, error="llm_unavailable: x"), MODEL)
    assert not cache.cacheable(result(error="invalid_json: x", search_only=True), MODEL)
    assert not cache.cacheable(result(model="@cf/openai/gpt-oss-20b"), MODEL)  # fallback
    # A low-similarity abstention never called the LLM, so it has no model.
    assert not cache.cacheable(result(model="", abstained=True), MODEL)
    assert not cache.cacheable(result(model=""), "")


def test_disabled_cache_does_not_touch_the_database():
    class Explodes:
        def transaction(self):
            raise AssertionError("no database access expected")

    disabled = AnswerCache(Explodes(), ttl_days=0)
    assert disabled.get(key_for()) is None
    assert disabled.put(key_for(), {"answer": "x"}) is False


# --- against Postgres ----------------------------------------------------------------------

RESPONSE = {"answer": "Un término medio [1].", "citations": [{"n": 1}], "cached": False}


@pytest.mark.db
def test_put_and_get():
    with temp_schema_connection() as conn:
        answers = AnswerCache(conn, ttl_days=30)
        key = key_for()
        assert answers.get(key) is None
        assert answers.put(key, RESPONSE)
        assert answers.get(key) == RESPONSE
        assert answers.get(key_for("  ¿Qué es la   virtud ")) == RESPONSE
        assert answers.get(key_for(index_version="v2")) is None
        row = conn.execute(
            "SELECT hits, question_sha256, filters, model FROM answer_cache"
        ).fetchone()
        assert row[0] == 2 and row[1] == key.question_sha256
        assert row[2] == key.filters and row[3] == MODEL
        stored = conn.execute("SELECT answer_cache::text FROM answer_cache").fetchone()[0]
        assert "virtud" not in stored  # the question is only hashed


@pytest.mark.db
def test_put_replaces_and_entries_expire():
    with temp_schema_connection() as conn:
        answers = AnswerCache(conn, ttl_days=30)
        key = key_for()
        answers.put(key, RESPONSE)
        answers.put(key, {**RESPONSE, "answer": "Otra [1]."})
        assert answers.get(key)["answer"] == "Otra [1]."
        assert conn.execute("SELECT count(*) FROM answer_cache").fetchone()[0] == 1

        conn.execute("UPDATE answer_cache SET created_at = now() - interval '31 days'")
        assert answers.get(key) is None  # expired: a miss
        other = key_for("¿Qué es la amistad?")
        answers.put(other, RESPONSE)  # writing purges expired entries
        assert conn.execute("SELECT count(*) FROM answer_cache").fetchone()[0] == 1


@pytest.mark.db
def test_database_errors_are_a_miss_and_leave_the_connection_usable(caplog):
    with temp_schema_connection() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        # Only the test schema: `public` of DATABASE_URL may hold a migrated answer_cache.
        conn.execute(f"SET LOCAL search_path TO {schema}")
        conn.execute("DROP TABLE answer_cache")
        answers = AnswerCache(conn, ttl_days=30)
        with caplog.at_level(logging.WARNING, logger="app.cache"):
            assert answers.get(key_for()) is None
            assert answers.put(key_for(), RESPONSE) is False
        assert "Caché de respuestas no disponible" in caplog.text
        assert conn.execute("SELECT 1").fetchone()[0] == 1
