import pytest

from app import bekker, retrieval
from app.embeddings import FakeEmbedder
from app.index import build_index
from app.models import Chunk
from app.retrieval import (
    IndexNotReadyError,
    check_active_index,
    current_index_version,
    ensure_index_ready,
    get_chunk,
    neighbours,
    retrieve,
)
from tests.test_db_support import (
    EDITIONS,
    TEST_DIM,
    WORKS,
    fake_embedder,
    fake_settings,
    synthetic_units,
    temp_schema_connection,
)

# --- deduplication (no database) --------------------------------------------------------


def chunk(chunk_id: str, edition_id: str, span: str | None, work_id: str = "eth-nic") -> Chunk:
    start, end = bekker.parse_range(span) if span else (None, None)
    return Chunk(
        chunk_id=chunk_id,
        unit_id=chunk_id.split("#")[0],
        work_id=work_id,
        edition_id=edition_id,
        lang=edition_id.split(".")[1],
        divisions=[],
        seq=0,
        bekker_start=start,
        bekker_end=end,
        text="t",
        embed_text="t",
    )


def dedup(query_lang: str, *chunks: Chunk, min_overlap: int = 3) -> list[str]:
    d = retrieval._Deduper(query_lang, min_overlap)
    for i, c in enumerate(chunks):
        d.add(c, 1.0 - i / 10, "dense")
    return [hit.chunk.chunk_id for hit in d.results(10)]


def test_same_passage_in_two_languages_counts_once_in_the_query_language():
    en = chunk("en#1", "w.en.a", "1156a6-1156a30")
    es = chunk("es#1", "w.es.b", "1156a10-1156a35")
    other = chunk("en#2", "w.en.a", "1157a1-1157a20")
    assert dedup("es", en, other, es) == ["es#1", "en#2"]  # es takes en's place
    assert dedup("en", en, other, es) == ["en#1", "en#2"]
    assert dedup("en", es, en) == ["en#1"]


def test_dedup_rank_and_scores():
    d = retrieval._Deduper("es", 3)
    d.add(chunk("en#1", "w.en.a", "1156a6-1156a30"), 0.9, "dense")
    d.add(chunk("en#2", "w.en.a", "1157a1-1157a20"), 0.8, "dense")
    d.add(chunk("es#1", "w.es.b", "1156a10-1156a35"), 0.7, "dense")
    hits = d.results(5)
    assert [(h.chunk.chunk_id, h.rank, h.score) for h in hits] == [
        ("es#1", 1, 0.7),  # keeps its own score at the location's best position
        ("en#2", 2, 0.8),
    ]


def test_not_deduplicated():
    a = chunk("en#1", "w.en.a", "1156a6-1156a30")
    same_edition = chunk("en#2", "w.en.a", "1156a28-1156a40")
    touching = chunk("es#1", "w.es.b", "1156a29-1156a40")  # 2 shared lines < 3
    other_work = chunk("x#1", "x.es.b", "1156a6-1156a30", work_id="x")
    no_bekker = chunk("es#2", "w.es.b", None)
    assert dedup("es", a, same_edition) == ["en#1", "en#2"]
    assert dedup("es", a, touching) == ["en#1", "es#1"]
    assert dedup("es", a, other_work) == ["en#1", "x#1"]
    assert dedup("es", a, no_bekker) == ["en#1", "es#2"]


def test_overlap_counts_lines_across_a_column_break():
    a = chunk("en#1", "w.en.a", "1156a30-1156b5")
    b = chunk("es#1", "w.es.b", "1156b1-1156b20")  # 5 shared lines
    assert dedup("en", a, b) == ["en#1"]


# --- against Postgres -------------------------------------------------------------------


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(retrieval, "_catalogue", lambda: WORKS)
    with temp_schema_connection() as c:
        yield c


@pytest.fixture
def indexed(conn):
    build_index(
        synthetic_units(),
        works=WORKS,
        editions=EDITIONS,
        conn=conn,
        embedder=fake_embedder(),
        settings=fake_settings(),
        log=lambda _: None,
    )
    return conn


def ask(conn, query: str, **kwargs):
    return retrieve(query, conn=conn, embedder=fake_embedder(), **kwargs)


def ids(hits) -> list[str]:
    return [h.chunk.chunk_id for h in hits]


@pytest.mark.db
def test_bekker_reference_is_resolved_by_range(indexed):
    hits = ask(indexed, "¿Qué dice 1156a12?")
    assert hits[0].method == "bekker_ref"
    assert ids(hits) == ["eth-nic.es.test:8.3#1"]  # en and es twins count once; es kept
    assert ids(ask(indexed, "What does 1156a12 say?")) == ["eth-nic.en.test:8.3#1"]
    assert hits[0].rank == 1 and hits[0].score == 1.0


@pytest.mark.db
def test_bekker_range_returns_passages_in_order(indexed):
    hits = ask(indexed, "What is in 1115a1-1174b20?", lang="en")
    assert ids(hits) == [
        "eth-nic.en.test:3.6#1",
        "eth-nic.en.test:8.3#1",
        "eth-nic.en.test:10.4#1",
    ]
    assert {h.method for h in hits} == {"bekker_ref"}


@pytest.mark.db
def test_bekker_reference_outside_corpus_falls_back_to_dense(indexed):
    hits = ask(indexed, "What does 1450a5 say about courage in battle?")
    assert hits and hits[0].method == "dense"


@pytest.mark.db
def test_dense_ranks_the_matching_chunk_first(indexed):
    hits = ask(indexed, "the brave soldier faces danger and death in battle")
    assert hits[0].method == "dense"
    assert hits[0].chunk.chunk_id == "eth-nic.en.test:3.6#1"
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    scores = [h.score for h in hits]
    assert scores[0] == max(scores)
    hits_es = ask(indexed, "¿Qué es la amistad perfecta entre hombres buenos?")
    assert hits_es[0].chunk.chunk_id == "eth-nic.es.test:8.3#1"


@pytest.mark.db
def test_k_and_candidates(indexed):
    assert len(ask(indexed, "courage")) == 5  # settings.retrieval_k
    assert len(ask(indexed, "courage", k=2)) == 2
    # 7 chunks, 2 pairs of language twins: 5 distinct locations at most.
    assert len(ask(indexed, "courage", candidates=20)) == 5


@pytest.mark.db
def test_named_work_comes_first_then_fill(indexed):
    hits = ask(indexed, "What does the Politics say about courage and the brave soldier?", k=3)
    assert hits[0].chunk.work_id == "pol"
    assert len(hits) == 3  # one Politics chunk, filled with the best of the rest
    assert hits[1].chunk.chunk_id == "eth-nic.en.test:3.6#1"


@pytest.mark.db
def test_explicit_work_filter_is_hard(indexed):
    hits = ask(indexed, "courage and the brave soldier", works=["pol"], k=3)
    assert ids(hits) == ["pol.en.test:1.1253a#1"]


@pytest.mark.db
def test_language_filter(indexed):
    hits = ask(indexed, "courage valentía", lang="es", k=5)
    assert hits and {h.chunk.lang.value for h in hits} == {"es"}


@pytest.mark.db
def test_dubious_works_can_be_excluded(indexed):
    query = "household management of the estate, wife, slaves and wealth"
    assert ask(indexed, query)[0].chunk.work_id == "oec"
    hits = ask(indexed, query, include_dubious=False)
    assert hits and "oec" not in {h.chunk.work_id for h in hits}


@pytest.mark.db
def test_dense_dedup_across_languages(indexed):
    # Spanish question whose words favour the English chunk: the location keeps rank 1
    # but is represented by the Spanish chunk.
    hits = ask(indexed, "¿friendship philia utility pleasure virtue good men friends?")
    first = hits[0].chunk
    assert (first.bekker_start, first.lang.value) == (bekker.parse("1156a6").to_int(), "es")
    locations = [(h.chunk.work_id, h.chunk.bekker_start) for h in hits]
    assert len(locations) == len(set(locations))


@pytest.mark.db
def test_get_chunk_and_neighbours(indexed):
    c = get_chunk("eth-nic.en.test:8.3#1", conn=indexed)
    assert (
        c is not None
        and c.work_id == "eth-nic"
        and c.divisions == [("book", "8"), ("chapter", "3")]
    )
    assert c.embed_text.startswith("Aristotle, Nicomachean Ethics, book VIII, ch. 3, 1156a6–")
    assert get_chunk("nope#1", conn=indexed) is None
    assert ids_of(neighbours("eth-nic.en.test:8.3#1", conn=indexed)) == [
        "eth-nic.en.test:3.6#1",
        "eth-nic.en.test:10.4#1",
    ]
    assert ids_of(neighbours("eth-nic.en.test:3.6#1", conn=indexed)) == ["eth-nic.en.test:8.3#1"]
    assert neighbours("nope#1", conn=indexed) == []


def ids_of(chunks) -> list[str]:
    return [c.chunk_id for c in chunks]


@pytest.mark.db
def test_index_checks(indexed):
    version = current_index_version(conn=indexed)
    check = check_active_index(conn=indexed, settings=fake_settings())
    assert check.ok and check.index_version == version and check.chunk_count == 7
    bad = check_active_index(conn=indexed, settings=fake_settings(embedding_dim=TEST_DIM + 1))
    assert not bad.ok and any("dimensión" in p for p in bad.problems)
    wrong_model = check_active_index(
        conn=indexed, settings=fake_settings(embedding_provider="ollama")
    )
    assert not wrong_model.ok
    with pytest.raises(IndexNotReadyError):
        ensure_index_ready(conn=indexed, settings=fake_settings(embedding_dim=8))


@pytest.mark.db
def test_query_embedder_must_match_the_index(indexed):
    with pytest.raises(IndexNotReadyError):
        retrieve("courage", conn=indexed, embedder=FakeEmbedder(dim=TEST_DIM * 2))


@pytest.mark.db
def test_no_index_yet(conn):
    with pytest.raises(IndexNotReadyError):
        ask(conn, "courage")
    assert not check_active_index(conn=conn, settings=fake_settings()).ok


@pytest.mark.db
def test_check_leaves_a_caller_connection_usable(conn):
    conn.execute("DROP TABLE chunks, index_versions CASCADE")
    check = check_active_index(conn=conn, settings=fake_settings())
    assert not check.ok
    assert conn.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.db
def test_wide_and_repeated_bekker_ranges_fetch_a_bounded_number_of_rows(indexed, monkeypatch):
    # "100a-1462b" covers every chunk; repeated 40 times it used to fetch the whole index 40
    # times and deduplicate it in quadratic time.
    added = []
    original = retrieval._Deduper.add

    def counting(self, chunk, score, method):
        added.append(chunk.chunk_id)
        return original(self, chunk, score, method)

    monkeypatch.setattr(retrieval._Deduper, "add", counting)
    hits = ask(indexed, " ".join(["100a-1462b"] * 40), k=1)
    assert len(hits) == 1 and hits[0].method == "bekker_ref"
    assert len(added) <= retrieval._FETCH_FACTOR


@pytest.mark.db
def test_only_the_first_bekker_references_are_resolved(indexed):
    from app.settings import get_settings

    cap = get_settings().retrieval_max_bekker_refs
    outside = [f"{1400 + i}a1" for i in range(cap)]  # outside the synthetic corpus
    assert ask(indexed, " ".join([*outside, "1156a12"]))[0].method == "dense"  # past the cap
    assert ask(indexed, " ".join([*outside[:-1], "1156a12"]))[0].method == "bekker_ref"


@pytest.mark.db
def test_bekker_path_also_refuses_an_index_of_another_model(indexed):
    # The Bekker path needs no embedding, but must not serve a mismatched index (rule 4).
    with pytest.raises(IndexNotReadyError):
        retrieve("¿Qué dice 1156a12?", conn=indexed, embedder=FakeEmbedder(dim=TEST_DIM * 2))
