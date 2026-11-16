"""evals.run end to end with a stub retriever, answerer and judge: no database, no LLM."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.models import AskResult
from app.settings import Settings
from evals import run as runner
from evals.cost import QuotaExhausted
from evals.judge import Judge, load_prompt
from evals.report import load_run
from tests.test_evals_support import (
    CORPUS_VERSION,
    SYNTHETIC_TEXT,
    WORKS,
    abstain_case,
    bspan,
    case,
    chunk,
    citation,
    hits,
    units,
    write_dataset,
)

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
SECRET = "CORPUS TEXT THAT MUST NOT BE WRITTEN"


def settings(**kw) -> Settings:
    return Settings(bootstrap_resamples=2000, **kw)


def dataset(tmp_path):
    d = tmp_path / "dataset"
    write_dataset(
        d,
        [
            case("d1"),
            case(
                "d2",
                type="synthesis",
                evidence=[[bspan("1156a10", "1156a20")], [bspan("1177a20", "1177a30")]],
            ),
            abstain_case("d3"),
            case("d4", revisado_por=None, fecha_revision=None, source="synthetic_unreviewed"),
            case("t1", split="test", evidence=[[bspan("1343a3", "1343a9", "oec")]]),
        ],
    )
    return d


def c(chunk_id: str, span: str, **kw):
    return chunk(chunk_id, span, text=SECRET, **kw)


FILLER = [c(f"f#{i}", f"1094a{i}-1094a{i + 1}") for i in range(1, 30)]
RETRIEVED = {
    "Pregunta de prueba d1": hits(
        c("x#1", "1100a1-1100a20"), c("x#2", "1106b30-1107a10"), *FILLER[:18]
    ),
    "Pregunta de prueba d2": hits(
        c("y#1", "1156a6-1156a30"), *FILLER[:5], c("y#2", "1177a12-1177b5"), *FILLER[5:18]
    ),
    "Pregunta de prueba d3": hits(*FILLER[:20], start_score=0.31),
    "Pregunta de prueba t1": hits(c("z#1", "1343a1-1343a20", work_id="oec", edition_id="oec.en.t")),
}


def retriever(question):
    return RETRIEVED[question]


def inputs(tmp_path, split="dev", confirm=False, s=None, data_dir=None):
    return runner.prepare_inputs(
        split=split,
        confirm_test=confirm,
        dataset_dir=data_dir or dataset(tmp_path),
        settings=s or settings(),
        works=WORKS,
        units=units(),
        corpus_version=CORPUS_VERSION,
        log=lambda _: None,
    )


def run_retrieval(tmp_path, *, now=NOW, split="dev", confirm=False, s=None):
    s = s or settings()
    return runner.run_retrieval_eval(
        inputs(tmp_path, split, confirm, s),
        retriever=retriever,
        results_dir=tmp_path / "results",
        index_version="iv-test",
        embedding={"model": s.embedding_model, "provider": "stub", "dim": 4},
        now=now,
        log=lambda _: None,
    )


def test_retrieval_mode_end_to_end(tmp_path):
    run_dir = run_retrieval(tmp_path)
    assert run_dir.name.startswith("20261116-120000_")
    run = load_run(run_dir)
    meta = run.meta
    assert meta["mode"] == "retrieval" and meta["split"] == "dev"
    assert meta["dataset"]["n_reviewed"] == 4 and meta["dataset"]["n_ignored_unreviewed"] == 1
    assert meta["dataset"]["n_split"] == 3
    assert meta["index_version"] == "iv-test" and meta["corpus_version"] == "cv-test"
    assert meta["bootstrap"] == {"seed": 1, "resamples": 2000}
    assert meta["settings"]["llm_api_key"] in ("", "***")
    assert len(meta["git"]["sha"]) in (0, 40)

    by_id = run.by_id
    assert by_id["d1"]["first_hit_ranks"] == [2]
    assert by_id["d1"]["recall_at_k"] == 1.0 and by_id["d1"]["rr_at_10"] == 0.5
    assert by_id["d2"]["first_hit_ranks"] == [1, 7]
    assert by_id["d2"]["recall_at_k"] == 0.5 and by_id["d2"]["recall_at_candidates"] == 1.0
    assert by_id["d2"]["rr_at_10"] is None
    assert by_id["d3"]["scored"] is False
    assert (by_id["d3"]["gate"], by_id["d3"]["gate_score"]) == ("score", 0.3)

    summary = meta["summary"]
    assert summary["n_scored"] == 2
    assert summary["metrics"]["recall_at_k"]["value"] == 0.75
    assert summary["metrics"]["recall_at_candidates"]["value"] == 1.0
    assert summary["metrics"]["all_evidence_at_k"] == pytest.approx(
        {"value": 0.5, "lo": 0.094531, "hi": 0.905469, "n": 2, "method": "wilson"}, abs=1e-5
    )
    assert summary["metrics"]["rr_at_10"]["n"] == 1

    summary_md = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "Recall de evidencia @5 | 75,0 %" in summary_md
    assert "ignorados por no tener revisado_por: 1." in summary_md
    # Results keep ids and ranges, never corpus text.
    for name in ("run.json", "per_question.jsonl", "summary.md"):
        assert SECRET not in (run_dir / name).read_text(encoding="utf-8")
    latest = json.loads((tmp_path / "results" / runner.LATEST_NAME).read_text(encoding="utf-8"))
    assert latest["config_hash"] == meta["config_hash"]
    assert latest["metrics"] == summary["metrics"]


def test_two_runs_give_identical_numbers(tmp_path):
    a = load_run(run_retrieval(tmp_path))
    latest_a = (tmp_path / "results" / runner.LATEST_NAME).read_bytes()
    b = load_run(run_retrieval(tmp_path, now=NOW + timedelta(seconds=1)))
    latest_b = (tmp_path / "results" / runner.LATEST_NAME).read_bytes()
    assert a.path != b.path
    assert a.meta["summary"] == b.meta["summary"]
    assert a.records == b.records
    assert latest_a == latest_b


def test_test_split_needs_confirmation_and_is_counted(tmp_path):
    with pytest.raises(runner.RunError, match="--confirm-test"):
        inputs(tmp_path, split="test")
    run_retrieval(tmp_path, split="test", confirm=True)
    run_retrieval(tmp_path, split="test", confirm=True, now=NOW + timedelta(seconds=1))
    counter = json.loads((tmp_path / "results" / runner.TEST_COUNTER_NAME).read_text("utf-8"))
    assert counter["count"] == 2 and len(counter["runs"]) == 2
    assert not (tmp_path / "results" / runner.LATEST_NAME).exists()  # only dev writes it


def test_partial_runs_do_not_replace_the_latest_summary(tmp_path):
    s = settings()
    part = runner.prepare_inputs(
        split="dev",
        confirm_test=False,
        dataset_dir=dataset(tmp_path),
        settings=s,
        works=WORKS,
        units=units(),
        corpus_version=CORPUS_VERSION,
        limit=1,
        log=lambda _: None,
    )
    assert [c.id for c in part.cases] == ["d1"]
    run_dir = runner.run_retrieval_eval(
        part,
        retriever=retriever,
        results_dir=tmp_path / "r",
        index_version="iv",
        embedding={},
        now=NOW,
        log=lambda _: None,
    )
    assert load_run(run_dir).meta["limit"] == 1
    assert "corrida parcial" in load_run(run_dir).meta["warnings"][0]
    assert not (tmp_path / "r" / runner.LATEST_NAME).exists()


def prepare(tmp_path, **kw):
    return runner.prepare_inputs(
        split=kw.pop("split", "dev"),
        confirm_test=kw.pop("confirm_test", False),
        dataset_dir=dataset(tmp_path),
        settings=settings(),
        works=WORKS,
        units=units(),
        corpus_version=CORPUS_VERSION,
        log=lambda _: None,
        **kw,
    )


def test_ids_file_selects_cases_in_its_order_and_is_partial(tmp_path):
    ids_file = tmp_path / "subset.txt"
    ids_file.write_text("# PR subset\nd3\n\nd1\nd4\n", encoding="utf-8")
    part = prepare(tmp_path, ids=runner.read_ids_file(ids_file), ids_file="subset.txt")
    assert [c.id for c in part.cases] == ["d3", "d1"]  # d4 is unreviewed: skipped
    run_dir = runner.run_retrieval_eval(
        part,
        retriever=retriever,
        results_dir=tmp_path / "r",
        index_version="iv",
        embedding={},
        now=NOW,
        log=lambda _: None,
    )
    meta = load_run(run_dir).meta
    assert meta["ids_file"] == "subset.txt"
    assert any("subset.txt" in w for w in meta["warnings"])
    assert not (tmp_path / "r" / runner.LATEST_NAME).exists()


def test_ids_file_rejects_unknown_ids_and_other_split(tmp_path):
    with pytest.raises(runner.RunError, match="no existen: nope"):
        prepare(tmp_path, ids=["d1", "nope"])
    with pytest.raises(runner.RunError, match="no son de dev: t1"):
        prepare(tmp_path, ids=["t1"])
    with pytest.raises(runner.RunError, match="No existe el archivo"):
        runner.read_ids_file(tmp_path / "missing.txt")


def test_include_unreviewed_is_labelled_and_never_the_latest_summary(tmp_path):
    with pytest.raises(runner.RunError, match="solo se permite sobre dev"):
        prepare(tmp_path, split="test", confirm_test=True, include_unreviewed=True)
    part = prepare(tmp_path, include_unreviewed=True)
    assert [c.id for c in part.cases] == ["d1", "d2", "d3", "d4"]

    def any_question(question):
        return RETRIEVED.get(question) or hits(*FILLER[:20])

    run_dir = runner.run_retrieval_eval(
        part,
        retriever=any_question,
        results_dir=tmp_path / "r",
        index_version="iv",
        embedding={},
        now=NOW,
        log=lambda _: None,
    )
    meta = load_run(run_dir).meta
    assert meta["dataset"]["includes_unreviewed"] is True
    assert meta["dataset"]["n_unreviewed_included"] == 1
    assert meta["warnings"][0] == runner.UNREVIEWED_NOTE
    summary_md = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "INCLUYE CASOS SIN REVISAR" in summary_md.splitlines()[0]
    assert "1 están SIN REVISAR" in summary_md
    assert not (tmp_path / "r" / runner.LATEST_NAME).exists()


def test_full_run_stops_when_the_llm_keeps_failing(tmp_path):
    def broken(question):
        return AskResult(
            answer="Solo búsqueda.",
            answer_lang="es",
            abstained=False,
            search_only=True,
            error="llm_error: HTTP 401",
        )

    with pytest.raises(runner.RunError, match="Revisar LLM_"):
        runner.run_full_eval(
            inputs(tmp_path),
            answerer=broken,
            generator_llm=StubLLM(""),
            judge=None,
            results_dir=tmp_path / "r",
            index_version="iv",
            embedding={},
            now=NOW,
            log=lambda _: None,
        )


def test_invalid_labels_stop_the_run(tmp_path):
    d = tmp_path / "bad"
    write_dataset(d, [case("x", evidence=[[bspan("1110a1", "1110a9")]])])  # not in the corpus
    with pytest.raises(runner.RunError, match="no resuelve a texto del corpus"):
        inputs(tmp_path, data_dir=d)


def test_check_latest(tmp_path):
    run_retrieval(tmp_path)
    results, data = tmp_path / "results", tmp_path / "dataset"
    problems, _ = runner.check_latest(settings(), dataset_dir=data, results_dir=results)
    assert problems == []
    problems, _ = runner.check_latest(
        settings(retrieval_k=6), dataset_dir=data, results_dir=results
    )
    assert "no corresponde a la configuración del commit" in problems[0]
    latest = json.loads((results / runner.LATEST_NAME).read_text(encoding="utf-8"))
    main = tmp_path / "main.json"
    better = {**latest, "metrics": {**latest["metrics"], "recall_at_k": {"value": 0.9}}}
    main.write_text(json.dumps(better), encoding="utf-8")
    problems, _ = runner.check_latest(
        settings(), dataset_dir=data, results_dir=results, baseline_file=main
    )
    assert problems and "bajó respecto de main" in problems[0]
    main.write_text(json.dumps({**better, "dataset_version": "other"}), encoding="utf-8")
    problems, notes = runner.check_latest(
        settings(), dataset_dir=data, results_dir=results, baseline_file=main
    )
    assert problems == [] and "no se compara" in notes[0]


def test_check_latest_before_the_dataset_exists(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    problems, notes = runner.check_latest(settings(), dataset_dir=empty, results_dir=tmp_path)
    assert problems == [] and "sin casos revisados" in notes[0]
    problems, _ = runner.check_latest(
        settings(), dataset_dir=dataset(tmp_path), results_dir=tmp_path / "nothing"
    )
    assert "falta retrieval_dev_latest.json" in problems[0]


def test_config_hash_tracks_retrieval_settings_and_dataset(tmp_path):
    s = settings()
    h = runner.config_hash(s, "v1", "retrieval")
    assert runner.config_hash(settings(), "v1", "retrieval") == h
    assert runner.config_hash(settings(embedding_provider="fake"), "v1", "retrieval") == h
    assert runner.config_hash(settings(chunk_max_words=301), "v1", "retrieval") != h
    assert runner.config_hash(s, "v2", "retrieval") != h
    assert runner.config_hash(settings(llm_model="x"), "v1", "retrieval") == h
    assert runner.config_hash(settings(llm_model="x"), "v1", "full") != runner.config_hash(
        s, "v1", "full"
    )


def test_redacted_settings():
    s = settings(llm_api_key="sk-secret", database_url="postgresql://u:pw@db:5432/x", eval_key="k")
    data = runner.redacted_settings(s)
    assert data["llm_api_key"] == "***" and data["eval_key"] == "***"
    assert data["database_url"] == "host=db port=5432 dbname=x user=u"
    assert "sk-secret" not in json.dumps(data)


@pytest.mark.parametrize(
    "url",
    [
        # Forms libpq accepts whose password urlsplit missed.
        "postgresql://postgres.abcd:s3cr#tPass@aws-0-eu.pooler.supabase.com:5432/postgres",
        "host=db.x.supabase.co user=postgres password=s3cr#tPass dbname=postgres",
        "postgresql://postgres@db.x.supabase.co/postgres?password=s3cr#tPass&sslmode=require",
        "postgresql://postgres@db.x.supabase.co/postgres?sslpassword=s3cr#tPass",
        "not a :// valid = dsn s3cr#tPass",
    ],
)
def test_database_password_never_reaches_run_json(url):
    value = runner.redacted_settings(settings(database_url=url))["database_url"]
    assert "s3cr" not in value and "tPass" not in value


# --- full mode --------------------------------------------------------------------------------


class StubLLM:
    """app.llm-like client returning scripted text; records every call."""

    def __init__(self, text, provider="stub"):
        self.text = text
        self.provider = provider
        self.model = "stub-model"
        self.calls = []
        self.hits = self.misses = 0
        self.exhausted = False

    def complete(self, messages, *, json_schema=None, max_tokens, temperature=0):
        self.calls.append(
            {"messages": messages, "json_schema": json_schema, "temperature": temperature}
        )
        self.misses += 1
        text = self.text(messages) if callable(self.text) else self.text
        return type(
            "R",
            (),
            {
                "text": text,
                "model": self.model,
                "provider": self.provider,
                "tokens_in": 100,
                "tokens_out": 20,
            },
        )()


def ask(question: str) -> AskResult:
    good = c("x#2", "1106b30-1107a10")
    other = c("x#1", "1100a1-1100a20")
    if question.endswith("d1"):
        return AskResult(
            answer="Es el justo medio de la virtud [1].",
            answer_lang="es",
            abstained=False,
            citations=[citation(1, good)],
            retrieved=hits(good, other),
            model="stub-model",
            provider="stub",
            tokens_in=900,
            tokens_out=80,
            prompt_version="p1",
        )
    return AskResult(
        answer="No lo encuentro.",
        answer_lang="es",
        abstained=True,
        abstain_reason="model_unanswerable",
        retrieved=hits(other),
        model="stub-model",
        provider="stub",
        prompt_version="p1",
    )


def judge(text='{"verdict": "pass", "reason": "ok"}', provider="stub"):
    return Judge(llm=StubLLM(text, provider), model_id="stub-judge", max_tokens=100)


def test_full_mode_end_to_end(tmp_path):
    j = judge()
    gen = StubLLM("unused")
    run_dir = runner.run_full_eval(
        inputs(tmp_path),
        answerer=ask,
        generator_llm=gen,
        judge=j,
        results_dir=tmp_path / "results",
        index_version="iv",
        embedding={"model": "m", "provider": "stub", "dim": 4},
        now=NOW,
        log=lambda _: None,
    )
    run = load_run(run_dir)
    d1, d2, d3 = (run.by_id[i] for i in ("d1", "d2", "d3"))
    assert d1["strict_pass"] is True and d1["citations_in_context"] is True
    assert d1["citation_check"]["groups_cited"] == 1 and d1["lang_ok"] is True
    assert set(d1["judge"]) == {"faithfulness", "key_points"}
    assert d2["strict_pass"] is False and d2["judge"]["key_points"]["source"] == "rule"
    assert d3["strict_pass"] is True and d3["judge"] == {}
    assert len(j.llm.calls) == 2  # only d1 needed the judge
    assert all(call["temperature"] == 0 and call["json_schema"] for call in j.llm.calls)
    assert SECRET in j.llm.calls[0]["messages"][1]["content"]  # the context went to the judge
    m = run.meta["summary"]["metrics"]
    assert m["false_refusal_rate"]["value"] == 0.5 and m["false_refusal_rate"]["n"] == 2
    assert m["false_answer_rate"]["value"] == 0.0 and m["false_answer_rate"]["n"] == 1
    assert m["strict_pass"]["value"] == pytest.approx(2 / 3)
    assert m["faithfulness"] == {**m["faithfulness"], "value": 1.0, "n": 1}
    models = run.meta["models"]
    assert models["judge"]["model"] == "stub-judge"
    assert set(models["judge"]["prompts"]) == {"faithfulness", "key_points", "premise_corrected"}
    assert models["generator"]["answered_by"] == [
        {"model": "stub-model", "provider": "stub", "n": 3}
    ]
    assert run.meta["prompt_versions"] == ["p1"] and run.meta["warnings"] == []
    for name in ("run.json", "per_question.jsonl", "summary.md"):
        assert SECRET not in (run_dir / name).read_text(encoding="utf-8")


def test_full_mode_flags_a_fake_judge(tmp_path):
    run_dir = runner.run_full_eval(
        inputs(tmp_path),
        answerer=ask,
        generator_llm=StubLLM(""),
        judge=judge(provider="fake"),
        results_dir=tmp_path / "r",
        index_version="iv",
        embedding={},
        now=NOW,
        log=lambda _: None,
    )
    assert any("juez falso" in w for w in load_run(run_dir).meta["warnings"])


def test_full_mode_stops_on_exhausted_quota(tmp_path):
    gen = StubLLM("")
    gen.exhausted = True
    with pytest.raises(QuotaExhausted):
        runner.run_full_eval(
            inputs(tmp_path),
            answerer=ask,
            generator_llm=gen,
            judge=None,
            results_dir=tmp_path / "r",
            index_version="iv",
            embedding={},
            now=NOW,
            log=lambda _: None,
        )
    assert not (tmp_path / "r").exists()


def test_full_mode_without_judge(tmp_path):
    run_dir = runner.run_full_eval(
        inputs(tmp_path),
        answerer=ask,
        generator_llm=StubLLM(""),
        judge=None,
        results_dir=tmp_path / "r",
        index_version="iv",
        embedding={},
        now=NOW,
        log=lambda _: None,
    )
    run = load_run(run_dir)
    assert run.by_id["d1"]["strict_pass"] is None and run.by_id["d1"]["judge"] is None
    # Without a judge no case gets a strict pass (not only the ones the rules settle).
    assert all(r["strict_pass"] is None for r in run.records)
    assert run.meta["summary"]["metrics"]["strict_pass"]["n"] == 0
    assert run.meta["summary"]["metrics"]["behavior_ok"]["n"] == 3


# --- baseline without retrieval -------------------------------------------------------------------


def test_baseline_mode(tmp_path):
    def answer(messages):
        q = messages[-1]["content"]
        if q.endswith("d3"):
            return '{"answerable": false, "answer": "No lo sé."}'
        return '```json\n{"answerable": true, "answer": "Lo dice en 1106b36 y en 1200a1."}\n```'

    llm = StubLLM(answer)
    j = judge()
    prompt = load_prompt(runner.PROMPTS_DIR / runner.BASELINE_PROMPT)
    run_dir = runner.run_baseline_eval(
        inputs(tmp_path),
        llm=llm,
        prompt=prompt,
        judge=j,
        results_dir=tmp_path / "r",
        now=NOW,
        log=lambda _: None,
    )
    run = load_run(run_dir)
    d1 = run.by_id["d1"]
    assert d1["bekker_refs"] == ["1106b36", "1200a1"] and d1["bekker_ref_precision"] == 0.5
    assert d1["parse_ok"] is True and d1["mentions_bekker"] is True
    assert run.by_id["d3"]["abstained"] is True and run.by_id["d3"]["behavior_ok"] is True
    # Faithfulness of the baseline is judged against the expected passages' text.
    faithfulness_call = j.llm.calls[0]["messages"][1]["content"]
    assert SYNTHETIC_TEXT.format(n=1) in faithfulness_call
    assert run.meta["models"]["generator"]["prompt"].startswith("baseline_no_retrieval_v1@")
    for name in ("run.json", "per_question.jsonl", "summary.md"):
        assert SYNTHETIC_TEXT.format(n=1) not in (run_dir / name).read_text(encoding="utf-8")


def test_similarity_gate_matches_the_generation_rule():
    dense = hits(c("a#1", "1100a1-1100a9"), c("a#2", "1100a10-1100a19"), start_score=0.8)
    assert runner.similarity_gate(dense, 5) == {"gate": "score", "gate_score": 0.79}
    ref = [h.model_copy(update={"method": "bekker_ref", "score": 1.0}) for h in dense]
    assert runner.similarity_gate(ref, 5)["gate"] == "always_pass"
    assert runner.similarity_gate([], 5)["gate"] == "always_abstain"
    # The best dense score among the first k, not the rank-1 score (dedup can reorder).
    swapped = [dense[0].model_copy(update={"score": 0.5}), dense[1]]
    assert runner.similarity_gate(swapped, 5)["gate_score"] == 0.78
    assert runner.similarity_gate(swapped, 1)["gate_score"] == 0.5


def test_parse_baseline():
    assert runner.parse_baseline('{"answerable": false, "answer": "no"}') == ("no", True, True)
    assert runner.parse_baseline("texto libre") == ("texto libre", False, False)


def test_unanswerable_reviews_expire_when_the_corpus_changes(tmp_path):
    # PLAN: unanswerable cases are reviewed again whenever corpus_version changes.
    d = tmp_path / "dataset"
    write_dataset(
        d,
        [
            case("d1"),
            abstain_case("u-current"),
            abstain_case("u-old", corpus_version_checked="0000000000000000"),
            abstain_case("u-never", corpus_version_checked=None),
            case("a-old", corpus_version_checked="0000000000000000"),  # answerable: kept
        ],
    )
    logs = []
    part = runner.prepare_inputs(
        split="dev",
        confirm_test=False,
        dataset_dir=d,
        settings=settings(),
        works=WORKS,
        units=units(),
        corpus_version=CORPUS_VERSION,
        log=logs.append,
    )
    assert [c.id for c in part.cases] == ["d1", "u-current", "a-old"]
    assert part.review_expired == ["u-old", "u-never"]
    assert "revisión vencida: 2" in logs[0]
    assert any("revisión vencida" in note for note in part.notes)
    run_dir = runner.run_retrieval_eval(
        part,
        retriever=lambda q: hits(*FILLER[:20]),
        results_dir=tmp_path / "r",
        index_version="iv",
        embedding={},
        now=NOW,
        log=lambda _: None,
    )
    assert load_run(run_dir).meta["dataset"]["n_review_expired"] == 2
    # With --include-unreviewed they run, counted as unreviewed.
    everything = runner.prepare_inputs(
        split="dev",
        confirm_test=False,
        dataset_dir=d,
        settings=settings(),
        works=WORKS,
        units=units(),
        corpus_version=CORPUS_VERSION,
        include_unreviewed=True,
        log=lambda _: None,
    )
    assert len(everything.cases) == 5 and everything.n_unreviewed == 2
