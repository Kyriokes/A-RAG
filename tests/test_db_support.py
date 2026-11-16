"""Helpers for the Postgres tests (no tests here).

Every DB test runs in a throwaway schema created on the test database (tests/conftest.py:
TEST_DATABASE_URL, else DATABASE_URL, and never a remote host or ENV=prod), with the
migrations applied inside it, and dropped afterwards. Real tables (a local index) are never
touched. When Postgres is unreachable or the database was refused the tests are skipped.

Also: a small synthetic corpus written for the tests (it is not Aristotle's text).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from pgvector.psycopg import register_vector

from app import bekker
from app.db import migrate
from app.embeddings import FakeEmbedder
from app.models import BekkerPrecision, Edition, Unit, Work
from app.settings import Settings
from tests import conftest

TEST_DIM = 256


def db_url() -> str:
    """The vetted test database URL; skips the test when conftest refused one."""
    if conftest.TEST_DATABASE_URL is None:
        pytest.skip(f"Tests de base desactivados: {conftest.TEST_DATABASE_REFUSED}")
    return conftest.TEST_DATABASE_URL


@contextmanager
def temp_schema_connection() -> Iterator[psycopg.Connection]:
    url = db_url()
    try:
        conn = psycopg.connect(url, connect_timeout=3, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres no disponible en DATABASE_URL: {exc}")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    try:
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except psycopg.Error as exc:
            pytest.skip(f"pgvector no disponible: {exc}")
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}, public")
        register_vector(conn)
        conn.autocommit = False
        migrate(conn)
        yield conn
    finally:
        if not conn.closed:
            conn.rollback()
            conn.autocommit = True
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.close()


def fake_settings(**overrides) -> Settings:
    values = {
        "embedding_provider": "fake",
        "embedding_dim": TEST_DIM,
        "embedding_normalize": True,
        "active_index_version": "",
    }
    values.update(overrides)
    return Settings(**values)


def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder(dim=TEST_DIM)


# --- synthetic corpus ------------------------------------------------------------------

WORKS = {
    "eth-nic": Work(
        work_id="eth-nic",
        abbr="EN",
        title_es="Ética a Nicómaco",
        title_en="Nicomachean Ethics",
        title_la="Ethica Nicomachea",
        authenticity="genuine",
        structure="book_chapter",
    ),
    "pol": Work(
        work_id="pol",
        abbr="Pol.",
        title_es="Política",
        title_en="Politics",
        title_la="Politica",
        authenticity="genuine",
        structure="book_bekker_page",
    ),
    "oec": Work(
        work_id="oec",
        abbr="Oec.",
        title_es="Económicos",
        title_en="Economics",
        title_la="Oeconomica",
        authenticity="disputed",
        structure="book_chapter",
    ),
}


def edition(edition_id: str, work_id: str, lang: str, status: str = "indexed") -> Edition:
    return Edition(
        edition_id=edition_id,
        work_id=work_id,
        lang=lang,
        translator="Test Translator",
        url="https://example.org/test",
        pinned_version="test",
        licence="CC0",
        licence_evidence="written for the tests",
        attribution="test fixture",
        adapter="test",
        tier=0,
        has_bekker=True,
        status=status,
    )


EDITIONS = [
    edition("eth-nic.en.test", "eth-nic", "en"),
    edition("eth-nic.es.test", "eth-nic", "es"),
    edition("pol.en.test", "pol", "en"),
    edition("oec.en.test", "oec", "en"),
    edition("eth-nic.grc.test", "eth-nic", "grc", status="reference"),
]

# (edition_id, divisions, bekker range, text). Two sentences repeated to give each unit
# about 60-90 words, so every unit becomes exactly one chunk with default settings.
_UNITS = [
    (
        "eth-nic.en.test",
        [("book", "3"), ("chapter", "6")],
        "1115a6-1115a35",
        "Courage is the mean between fear and confidence in the face of danger. "
        "The brave soldier stands firm in battle and faces death for a noble end. ",
    ),
    (
        "eth-nic.en.test",
        [("book", "8"), ("chapter", "3")],
        "1156a6-1156a35",
        "Friendship or philia is of three kinds: for utility, for pleasure and for virtue. "
        "Perfect friendship is between good men who wish good to their friends. ",
    ),
    (
        "eth-nic.en.test",
        [("book", "10"), ("chapter", "4")],
        "1174a13-1174b14",
        "Pleasure completes the activity as a bloom completes the young. "
        "Every pleasure belongs to an activity of sight, thought or contemplation. ",
    ),
    (
        "eth-nic.es.test",
        [("book", "3"), ("chapter", "6")],
        "1115a6-1115a35",
        "La valentía es el término medio entre el miedo y la confianza ante el peligro. "
        "El soldado valiente resiste en la batalla y enfrenta la muerte por un fin noble. ",
    ),
    (
        "eth-nic.es.test",
        [("book", "8"), ("chapter", "3")],
        "1156a6-1156a35",
        "La amistad o philia es de tres clases: por utilidad, por placer y por virtud. "
        "La amistad perfecta es entre hombres buenos que quieren el bien de sus amigos. ",
    ),
    (
        "pol.en.test",
        [("book", "1"), ("bekker_page", "1253a")],
        "1253a1-1253a30",
        "The city exists by nature and man is by nature a political animal. "
        "Citizens share in courage and justice, and the city comes before the household. ",
    ),
    (
        "oec.en.test",
        [("book", "1"), ("chapter", "1")],
        "1343a1-1343a25",
        "Household management concerns the estate, the wife, the slaves and wealth. "
        "The master of the household must acquire property and preserve it. ",
    ),
]


def synthetic_units(repeat: int = 4) -> list[Unit]:
    units: list[Unit] = []
    seq_by_edition: dict[str, int] = {}
    for edition_id, divisions, span, sentence in _UNITS:
        start, end = bekker.parse_range(span)
        seq = seq_by_edition.get(edition_id, 0) + 1
        seq_by_edition[edition_id] = seq
        units.append(
            Unit(
                unit_id=f"{edition_id}:{'.'.join(label for _, label in divisions)}",
                work_id=edition_id.split(".")[0],
                edition_id=edition_id,
                lang=edition_id.split(".")[1],
                divisions=divisions,
                seq=seq,
                bekker_start=start,
                bekker_end=end,
                bekker_precision=BekkerPrecision.interpolated,
                bekker_anchors=[(0, start)],
                text=(sentence * repeat).strip(),
            )
        )
    return units
