"""evals.run in retrieval mode through the real app.retrieval.retrieve(), on a throwaway
schema of the database in DATABASE_URL (skipped when Postgres is unreachable).

The corpus is a handful of invented sentences, not Aristotle's text.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import retrieval
from app.embeddings import FakeEmbedder
from app.index import build_index
from app.models import Edition
from app.settings import Settings
from evals import run as runner
from evals.report import load_run
from tests.test_db_support import temp_schema_connection
from tests.test_evals_support import CORPUS_VERSION, WORKS, bspan, case, units, write_dataset

DIM = 64
TEXTS = {
    "eth-nic.en.t:2.6": "Virtue is a settled disposition that observes the mean between excess "
    "and deficiency, as a wise person would determine it.",
    "eth-nic.en.t:8.3": "Friendship comes in three kinds, for utility, for pleasure and for "
    "goodness; only the friendship of good people lasts.",
    "eth-nic.en.t:10.7": "Contemplation is the highest activity, the most continuous and the "
    "most self-sufficient, and so it is complete happiness.",
    "oec.en.t:1.1": "Household management concerns the estate, its members and the "
    "acquisition and preservation of property.",
    "nobekker.es.t:3": "Capítulo de una obra sin numeración Bekker, escrito para las pruebas.",
}


def edition(edition_id: str) -> Edition:
    return Edition(
        edition_id=edition_id,
        work_id=edition_id.split(".")[0],
        lang=edition_id.split(".")[1],
        translator="Test",
        url="https://example.org/test",
        pinned_version="test",
        licence="CC0",
        licence_evidence="written for the tests",
        attribution="test fixture",
        adapter="test",
        tier=0,
        has_bekker=not edition_id.startswith("nobekker"),
        status="indexed",
    )


@pytest.mark.db
def test_retrieval_eval_through_the_real_retriever(tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval, "_catalogue", lambda: WORKS)
    corpus = [u.model_copy(update={"text": TEXTS[u.unit_id]}) for u in units()]
    embedder = FakeEmbedder(dim=DIM)
    data = tmp_path / "dataset"
    write_dataset(
        data,
        [
            case(
                "virtue",
                question="Is virtue the mean between excess and deficiency?",
                question_lang="en",
            ),
            case(
                "friends",
                question="What are the three kinds of friendship?",
                question_lang="en",
                evidence=[[bspan("1156a10", "1156a20")]],
            ),
            case(
                "home",
                question="What does household management concern?",
                question_lang="en",
                evidence=[[bspan("1343a3", "1343a9", "oec")]],
            ),
        ],
    )
    s = Settings(bootstrap_resamples=1000)
    with temp_schema_connection() as conn:
        build_index(
            corpus,
            works=WORKS,
            editions=[edition(e) for e in ("eth-nic.en.t", "oec.en.t", "nobekker.es.t")],
            conn=conn,
            embedder=embedder,
            settings=Settings(
                embedding_provider="fake", embedding_dim=DIM, active_index_version=""
            ),
            log=lambda _: None,
        )

        def retriever(question: str):
            return retrieval.retrieve(
                question, candidates=s.retrieval_candidates, conn=conn, embedder=embedder
            )

        runs = []
        for i in range(2):
            inputs = runner.prepare_inputs(
                split="dev",
                confirm_test=False,
                dataset_dir=data,
                settings=s,
                works=WORKS,
                units=corpus,
                corpus_version=CORPUS_VERSION,
                log=lambda _: None,
            )
            runs.append(
                load_run(
                    runner.run_retrieval_eval(
                        inputs,
                        retriever=retriever,
                        results_dir=tmp_path / "results",
                        index_version=retrieval.current_index_version(conn),
                        embedding={"model": embedder.name, "provider": "fake", "dim": DIM},
                        now=datetime(2026, 11, 16, tzinfo=UTC) + timedelta(seconds=i),
                        log=lambda _: None,
                    )
                )
            )
    first, second = runs
    assert first.meta["summary"] == second.meta["summary"]  # identical numbers
    assert first.meta["summary"]["metrics"]["recall_at_k"]["value"] == 1.0
    assert first.by_id["virtue"]["first_hit_ranks"] == [1]
    assert first.by_id["virtue"]["gate"] == "score"
    assert any("embeddings falsos" in w for w in first.meta["warnings"])
