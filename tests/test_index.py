import contextlib

import pytest

from app import index
from app.embeddings import EmbeddingError, FakeEmbedder
from app.index import IndexBuildError, build_index
from tests.test_db_support import (
    EDITIONS,
    TEST_DIM,
    WORKS,
    fake_embedder,
    fake_settings,
    synthetic_units,
    temp_schema_connection,
)

pytestmark = pytest.mark.db


@pytest.fixture
def conn():
    with temp_schema_connection() as c:
        yield c


def build(conn, units=None, *, settings=None, embedder=None, **kwargs):
    return build_index(
        synthetic_units() if units is None else units,
        works=WORKS,
        editions=EDITIONS,
        conn=conn,
        embedder=embedder or fake_embedder(),
        settings=settings or fake_settings(),
        log=lambda _: None,
        **kwargs,
    )


def fewer(units):
    """The corpus without one unit of an edition that keeps others."""
    return [u for u in units if u.unit_id != "eth-nic.en.test:10.4"]


def versions(conn) -> list[tuple[str, bool, int]]:
    return conn.execute(
        "SELECT index_version, active, chunk_count FROM index_versions ORDER BY created_at"
    ).fetchall()


def chunk_count(conn, version: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM chunks WHERE index_version = %s", (version,)
    ).fetchone()[0]


def test_build_writes_an_active_version(conn):
    result = build(conn)
    n_units = len(synthetic_units())
    assert result.status == "created"
    assert result.chunk_count == n_units  # each synthetic unit is one chunk
    assert result.embedded == n_units
    assert versions(conn) == [(result.index_version, True, n_units)]
    assert chunk_count(conn, result.index_version) == n_units
    row = conn.execute(
        "SELECT embedding_model, embedding_provider, embedding_dim, embedding_normalize "
        "FROM index_versions"
    ).fetchone()
    assert row == ("fake/hash-embedder-v1", "fake", TEST_DIM, True)
    ts = conn.execute("SELECT DISTINCT lang, ts_config::text FROM chunks ORDER BY 1").fetchall()
    assert ts == [("en", "english"), ("es", "spanish")]


def test_rebuilding_the_same_inputs_changes_nothing(conn):
    first = build(conn)
    second = build(conn)
    assert second.status == "unchanged"
    assert second.index_version == first.index_version
    assert second.embedded == 0
    assert len(versions(conn)) == 1
    assert chunk_count(conn, first.index_version) == first.chunk_count


def test_cli_prints_count_version_and_sin_cambios(conn, monkeypatch, capsys):
    units = synthetic_units()
    monkeypatch.setattr(index, "load_indexed_corpus", lambda: (WORKS, EDITIONS, units, "c1"))
    monkeypatch.setattr(index, "connect", lambda settings=None: contextlib.nullcontext(conn))
    monkeypatch.setattr(index, "get_settings", fake_settings)
    monkeypatch.setattr(index, "get_embedder", lambda settings=None: fake_embedder())

    assert index.main([]) == 0
    out1 = capsys.readouterr().out
    assert index.main([]) == 0
    out2 = capsys.readouterr().out

    version = versions(conn)[0][0]
    assert f"Fragmentos: {len(units)}" in out1  # one chunk per synthetic unit
    assert f"Versión de índice: {version}" in out1
    assert f"Versión de índice: {version}" in out2
    assert "sin cambios" in out2 and "sin cambios" not in out1


def test_new_corpus_version_reuses_cached_embeddings(conn):
    units = synthetic_units()
    first = build(conn, fewer(units))
    second = build(conn, units)
    assert second.index_version != first.index_version
    assert second.embedded == 1  # only the new unit's chunk goes to the provider
    assert [v[1] for v in versions(conn)] == [False, True]  # exactly one active


def test_failed_embedding_leaves_the_active_version_untouched(conn):
    units = synthetic_units()
    first = build(conn, fewer(units))

    class Failing(FakeEmbedder):
        def _embed_batch(self, texts):
            raise EmbeddingError("proveedor caído")

    with pytest.raises(EmbeddingError):
        build(conn, units, embedder=Failing(dim=TEST_DIM))
    assert versions(conn) == [(first.index_version, True, first.chunk_count)]


def test_switch_is_atomic(conn, monkeypatch):
    units = synthetic_units()
    first = build(conn, fewer(units))

    def broken_activate(conn, version):
        raise RuntimeError("fallo al activar")

    monkeypatch.setattr(index, "_activate", broken_activate)
    with pytest.raises(RuntimeError):
        build(conn, units)
    conn.rollback()
    # The new version and its chunks were rolled back with the failed switch.
    assert versions(conn) == [(first.index_version, True, first.chunk_count)]
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == first.chunk_count


def test_older_version_is_reactivated_without_embedding(conn):
    units = synthetic_units()
    first = build(conn, fewer(units))
    build(conn, units)
    again = build(conn, fewer(units))
    assert again.status == "reactivated"
    assert again.embedded == 0
    active = [v[0] for v in versions(conn) if v[1]]
    assert active == [first.index_version]


def test_refuses_model_or_dim_mismatch(conn):
    with pytest.raises(IndexBuildError, match="No se activa"):
        build(conn, settings=fake_settings(embedding_dim=TEST_DIM * 2))
    with pytest.raises(IndexBuildError, match="No se activa"):
        build(conn, settings=fake_settings(embedding_provider="ollama"))
    assert versions(conn) == []


def test_other_provider_needs_rebuild(conn):
    first = build(conn)

    class OtherProvider(FakeEmbedder):
        provider = "other"

    with pytest.raises(IndexBuildError, match="--rebuild"):
        build(conn, embedder=OtherProvider(dim=TEST_DIM))
    rebuilt = build(conn, embedder=OtherProvider(dim=TEST_DIM), rebuild=True)
    assert rebuilt.status == "rebuilt"
    assert rebuilt.index_version == first.index_version
    assert rebuilt.embedded == first.chunk_count  # cache entries were from another provider
    provider = conn.execute("SELECT embedding_provider FROM index_versions").fetchone()[0]
    assert provider == "other"
    assert chunk_count(conn, first.index_version) == first.chunk_count


def test_chunking_settings_change_the_version(conn):
    a = build(conn)
    b = build(conn, settings=fake_settings(chunk_max_words=250))
    assert a.index_version != b.index_version


def test_no_indexable_units(conn):
    with pytest.raises(IndexBuildError):
        build(conn, units=[])
