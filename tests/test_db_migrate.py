import numpy as np
import psycopg
import pytest

from app import db
from tests.test_db_support import temp_schema_connection

pytestmark = pytest.mark.db


@pytest.fixture
def conn():
    with temp_schema_connection() as c:
        yield c


def _tables(conn) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    return {r[0] for r in rows}


def test_migrate_creates_tables_and_is_idempotent(conn):
    assert {"index_versions", "chunks", "embedding_cache", "schema_migrations"} <= _tables(conn)
    applied = conn.execute("SELECT name FROM schema_migrations").fetchall()
    assert ("001_init.sql",) in applied
    assert db.migrate(conn) == []  # second run applies nothing


def test_changed_migration_is_an_error(conn, tmp_path):
    (tmp_path / "001_init.sql").write_text("SELECT 1; -- edited after being applied")
    with pytest.raises(db.MigrationError):
        db.migrate(conn, tmp_path)


def test_new_migration_is_applied_once(conn, tmp_path):
    for path in db.migration_files():
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "999_extra.sql").write_text("CREATE TABLE extra_test (id int);")
    assert db.migrate(conn, tmp_path) == ["999_extra.sql"]
    assert db.migrate(conn, tmp_path) == []
    assert "extra_test" in _tables(conn)


def _add_version(conn, version: str, dim: int, active: bool = False) -> None:
    conn.execute(
        """
        INSERT INTO index_versions (index_version, embedding_model, embedding_provider,
            embedding_dim, embedding_normalize, corpus_version, chunking_config, chunk_count,
            active)
        VALUES (%s, 'm', 'fake', %s, true, 'c', '{}', 0, %s)
        """,
        (version, dim, active),
    )


def _add_chunk(conn, version: str, vector) -> None:
    conn.execute(
        """
        INSERT INTO chunks (index_version, chunk_id, unit_id, work_id, edition_id, lang,
            divisions, seq, bekker_precision, text, embed_text, embed_sha256, embedding)
        VALUES (%s, 'u#1', 'u', 'w', 'e', 'en', '[]', 0, 'none', 't', 't', 'x', %s)
        """,
        (version, np.asarray(vector, dtype=np.float32)),
    )


def test_trigger_rejects_wrong_dimension(conn):
    _add_version(conn, "v1", dim=3)
    _add_chunk(conn, "v1", [1, 0, 0])  # right dimension is accepted
    with pytest.raises(psycopg.errors.RaiseException):
        _add_chunk(conn, "v1", [1, 0])


def test_at_most_one_active_version(conn):
    _add_version(conn, "v1", dim=3, active=True)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _add_version(conn, "v2", dim=3, active=True)


def test_cli_rejects_unknown_command(capsys):
    assert db.main(["bogus"]) == 2
    assert "migrate" in capsys.readouterr().err
