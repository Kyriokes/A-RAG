"""compare, report and smoke."""

from datetime import timedelta
from pathlib import Path

import pytest

from app.models import AskResult
from evals import compare, report, smoke
from evals.report import RunData, load_run
from evals.schema import SmokeCase
from tests.test_evals_run import NOW, StubLLM, ask, inputs, judge, run_retrieval
from tests.test_evals_support import chunk, citation, hits


def run_data(name: str, values: list, *, usage: dict | None = None) -> RunData:
    records = [
        {"id": f"q{i}", "strict_pass": v, "cluster": f"c{i // 2}"} for i, v in enumerate(values)
    ]
    meta = {
        "mode": "full",
        "split": "dev",
        "summary": {"usage": usage or {}},
        "dataset": {"version": "v"},
    }
    return RunData(Path(name), meta, records)


def test_compare_metric_pairs_by_question():
    a = run_data("a", [False] * 10 + [True] * 10)
    b = run_data("b", [True] * 10 + [True] * 9 + [False])
    c = compare.compare_metric(a, b, "strict_pass", resamples=2000, seed=1)
    assert (c.flips.fixed, c.flips.broken) == (10, 1)
    assert c.diff.diff == pytest.approx(0.45)
    assert c.diff.lo > 0
    assert c.p_value == pytest.approx(2 * 12 / 2048)
    clustered = compare.compare_metric(a, b, "strict_pass", resamples=2000, seed=1, cluster=True)
    assert clustered.diff.n_clusters == 10
    assert compare.compare_metric(a, b, "faithfulness", resamples=10, seed=1) is None


def test_decision_rule():
    a = run_data("a", [False] * 10 + [True] * 10, usage={"usd_per_1000_queries": 1.0})
    better = run_data("b", [True] * 20)
    same = run_data("c", [False] * 10 + [True] * 10, usage={"usd_per_1000_queries": 0.5})
    worse = run_data("d", [False] * 20)
    kw = {"resamples": 2000, "seed": 1}
    up = compare.compare_metric(a, better, "strict_pass", **kw)
    assert compare.decision(up, a, better, None).startswith("ADOPTAR")
    assert "menor que el ruido" in compare.decision(up, a, better, 0.6)
    flat = compare.compare_metric(a, same, "strict_pass", **kw)
    assert "baja costo" in compare.decision(flat, a, same, None)
    assert "Sin diferencia detectable" in compare.decision(flat, a, a, None)
    down = compare.compare_metric(a, worse, "strict_pass", **kw)
    assert compare.decision(down, a, worse, None).startswith("NO ADOPTAR")
    assert "no se puede decidir" in compare.decision(None, a, worse, None)


def test_compare_cli_on_two_retrieval_runs(tmp_path, capsys):
    first = run_retrieval(tmp_path)
    second = run_retrieval(tmp_path, now=NOW + timedelta(seconds=1))
    assert compare.main([str(first), str(second)]) == 0
    out = capsys.readouterr().out
    assert "| recall_at_k | 75,0 % | 75,0 % | +0,0 pts" in out
    assert "Decisión: Sin diferencia detectable en recall_at_k" in out


def test_report_block_and_markers(tmp_path):
    from evals import run as runner

    retrieval = load_run(run_retrieval(tmp_path))
    full = load_run(
        runner.run_full_eval(
            inputs(tmp_path),
            answerer=ask,
            generator_llm=StubLLM(""),
            judge=judge(),
            results_dir=tmp_path / "results",
            index_version="iv",
            embedding={"model": "m", "provider": "stub", "dim": 4},
            now=NOW,
            log=lambda _: None,
        )
    )
    block = report.results_block([retrieval, full], ["Retrieval base", "RAG base"], None)
    lines = block.splitlines()
    assert lines[0].startswith("| Sistema | Conjunto | Recall de evidencia @k")
    assert lines[2].startswith("| Retrieval base | dev | 75,0 % (50,0 %–100,0 %; n = 2)")
    assert "stub-model (stub)" in lines[3]
    assert "Métricas con juez sin calibrar" in block
    full.meta["warnings"] = ["LLM falso (LLM_PROVIDER=fake): las respuestas son simuladas"]
    assert "**Atención (RAG base)**: LLM falso" in report.results_block([full], ["RAG base"])
    agreement = {
        "criteria": {"faithfulness": {"agreement": 0.9, "n": 50}},
        "judge_model": "other",
        "prompts": {},
        "passed": True,
    }
    note = report.results_block([full], None, agreement)
    assert "faithfulness 90,0 % (n = 50)" in note and "calibración es de otro modelo" in note
    readme = f"intro\n{report.MARK_START}\nvieja\n{report.MARK_END}\nfin\n"
    updated = report.replace_block(readme, block)
    assert "vieja" not in updated and updated.startswith("intro\n") and updated.endswith("fin\n")
    with pytest.raises(ValueError):
        report.replace_block("sin marcadores", block)


# --- smoke ------------------------------------------------------------------------------------


def smoke_case(expected="answer") -> SmokeCase:
    return SmokeCase(id="s1", question="q", question_lang="es", expected_behavior=expected)


def test_smoke_checks():
    good = chunk("a#1", "1106b30-1107a10")
    ok = AskResult(
        answer="r [1]",
        answer_lang="es",
        abstained=False,
        citations=[citation(1, good)],
        retrieved=hits(good),
    )
    assert smoke.check_result(smoke_case(), ok) == []
    no_cite = ok.model_copy(update={"citations": []})
    assert smoke.check_result(smoke_case(), no_cite) == ["respuesta sin citas"]
    stray = ok.model_copy(update={"citations": [citation(1, chunk("z#9", "1106b30-1107a10"))]})
    assert "no recuperados: z#9" in smoke.check_result(smoke_case(), stray)[0]
    assert smoke.check_result(smoke_case("abstain"), ok) == ["debía abstenerse y respondió"]
    abstained = AskResult(answer="No lo encuentro.", answer_lang="es", abstained=True)
    assert smoke.check_result(smoke_case("abstain"), abstained) == []
    search_only = abstained.model_copy(update={"abstained": False, "search_only": True})
    assert "el LLM no respondió" in smoke.check_result(smoke_case(), search_only)[0]


def test_run_smoke_validates_the_schema():
    good = chunk("a#1", "1106b30-1107a10")
    ok = AskResult(
        answer="r",
        answer_lang="es",
        abstained=False,
        citations=[citation(1, good)],
        retrieved=hits(good),
    )
    logs = []
    assert smoke.run_smoke([smoke_case()], lambda q: ok.model_dump(mode="json"), logs.append) == 0
    assert smoke.run_smoke([smoke_case()], lambda q: {"answer": 1}, logs.append) == 1
    assert logs[-1].startswith("FALLA s1") and "esquema inválido" in logs[-1]


def test_smoke_over_http_uses_the_api_schema():
    from app.api import AskResponse

    good = chunk("a#1", "1106b30-1107a10")
    result = AskResult(
        answer="r",
        answer_lang="es",
        abstained=False,
        citations=[citation(1, good)],
        retrieved=hits(good),
    )
    body = AskResponse.from_result(result).model_dump(mode="json")
    assert smoke.run_smoke([smoke_case()], lambda q: body, lambda _: None, AskResponse) == 0
    stray = {**body, "retrieved": []}
    assert smoke.run_smoke([smoke_case()], lambda q: stray, lambda _: None, AskResponse) == 1


def test_smoke_without_file(tmp_path):
    assert smoke.main(["--file", str(tmp_path / "smoke.jsonl")]) == 1


def test_smoke_says_when_it_runs_on_fakes():
    from app.settings import Settings

    warnings = smoke.fake_warnings(Settings(llm_provider="fake", embedding_provider="fake"))
    assert len(warnings) == 2 and "no acepta la Etapa 4" in warnings[0]
    real = Settings(llm_provider="openai_compat", embedding_provider="ollama")
    assert smoke.fake_warnings(real) == []


def test_compare_does_not_decide_on_runs_that_only_test_the_code(tmp_path, capsys):
    import json

    first = run_retrieval(tmp_path)
    second = run_retrieval(tmp_path, now=NOW + timedelta(seconds=1))
    meta = json.loads((second / "run.json").read_text(encoding="utf-8"))
    meta["dataset"]["includes_unreviewed"] = True
    (second / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    assert compare.main([str(first), str(second)]) == 1
    out = capsys.readouterr().out
    assert "| recall_at_k |" in out  # the paired table is still shown
    assert "incluye casos sin revisar" in out
    assert "Decisión: no se decide" in out and "ADOPTAR" not in out


def test_strict_pass_needs_a_judge_in_both_runs():
    a = run_data("a", [True, False])
    b = run_data("b", [True, True])
    a.meta["summary"]["judged"] = True
    assert "necesita el juez" in compare.blockers(a, b, "strict_pass")[0]
    assert compare.blockers(a, b, "behavior_ok") == []
    b.meta["summary"]["judged"] = True
    assert compare.blockers(a, b, "strict_pass") == []


def full_run(tmp_path, *, judged=True, provider="stub"):
    from evals import run as runner

    return load_run(
        runner.run_full_eval(
            inputs(tmp_path),
            answerer=ask,
            generator_llm=StubLLM(""),
            judge=judge(provider=provider) if judged else None,
            results_dir=tmp_path / "results",
            index_version="iv",
            embedding={"model": "m", "provider": "stub", "dim": 4},
            now=NOW,
            log=lambda _: None,
        )
    )


def test_readme_withholds_judge_metrics_until_the_judge_is_calibrated(tmp_path):
    full = full_run(tmp_path)
    judge_meta = full.meta["models"]["judge"]
    calibrated = {
        "criteria": {"faithfulness": {"agreement": 0.9, "n": 50}},
        "judge_model": judge_meta["model"],
        "prompts": judge_meta["prompts"],
        "passed": True,
    }

    def row(agreement):
        return report.results_block([full], ["RAG"], agreement).splitlines()[2]

    for agreement in (None, {**calibrated, "passed": False}, {**calibrated, "judge_model": "x"}):
        cells = row(agreement).split(" | ")
        assert cells[3] == cells[4] == report.UNCALIBRATED_CELL  # strict pass, faithfulness
        assert "%" in cells[5]  # rule-based columns are still published
    assert report.UNCALIBRATED_CELL not in row(calibrated)


def test_readme_write_refuses_runs_that_only_test_the_code(tmp_path, capsys):
    full = full_run(tmp_path, provider="fake")
    readme = tmp_path / "README.md"
    readme.write_text(f"{report.MARK_START}\n{report.MARK_END}\n", encoding="utf-8")
    assert report.main([str(full.path), "--write", str(readme)]) == 1
    assert "juez falso" in capsys.readouterr().out
    assert readme.read_text(encoding="utf-8") == f"{report.MARK_START}\n{report.MARK_END}\n"
    assert report.main([str(full.path)]) == 0  # printing it is fine


def test_smoke_reports_unreachable_services_per_question():
    import httpx
    import psycopg

    from app.retrieval import IndexNotReadyError

    errors = iter(
        [
            psycopg.OperationalError("connection refused"),
            IndexNotReadyError("No hay índice activo"),
            httpx.ConnectError("refused"),
        ]
    )

    def broken(question):
        raise next(errors)

    logs = []
    cases = [smoke_case(), smoke_case(), smoke_case()]
    assert smoke.run_smoke(cases, broken, logs.append) == 3
    assert all(line.startswith("FALLA s1") for line in logs)
    assert "OperationalError" in logs[0] and "make index" in logs[0]
    assert "IndexNotReadyError" in logs[1]
    assert "ConnectError" in logs[2] and "la API" in logs[2]


def test_smoke_main_without_database_fails_cleanly(monkeypatch, capsys):
    import psycopg

    import app.generation

    def no_database(question):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(app.generation, "answer", no_database)
    assert smoke.main([]) == 1
    out = capsys.readouterr().out
    assert "0/10 preguntas pasan." in out and "make db" in out
