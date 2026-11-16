"""calibrate_abstention, judge_sample and judge_agreement, on synthetic run directories."""

import json
from pathlib import Path

import pytest

from evals import calibrate_abstention, judge_agreement, judge_sample
from evals.report import RunData


def write_run(path: Path, meta: dict, records: list[dict]) -> Path:
    path.mkdir(parents=True)
    meta = {"run_id": path.name, "split": "dev", "dataset": {"version": "v"}, "models": {}, **meta}
    (path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    (path / "per_question.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return path


# --- abstention threshold ---------------------------------------------------------------------


def test_choose_threshold_rejects_at_most_the_allowed_share():
    scores = [i / 20 for i in range(1, 21)]  # 0.05 .. 1.00
    t = calibrate_abstention.choose_threshold(scores, 0.05)
    assert t == 0.1
    assert calibrate_abstention.error_rates(scores, [], t)[0] == 0.05
    assert calibrate_abstention.error_rates(scores, [], t + 1e-4)[0] > 0.05  # it is the highest
    assert calibrate_abstention.choose_threshold([0.5] * 10 + [0.9] * 10, 0.05) == 0.5
    assert calibrate_abstention.choose_threshold([0.7, 0.3, 0.9], 0.05) == 0.3  # rejects none
    assert calibrate_abstention.choose_threshold([0.123456, 0.9], 0.0) == 0.1234  # rounded down
    assert calibrate_abstention.choose_threshold([], 0.05) is None


def test_choose_threshold_with_gate_extremes():
    inf = float("inf")
    # Questions answered by Bekker reference always pass; without passages, always abstain.
    assert calibrate_abstention.choose_threshold([-inf] + [0.6] * 19, 0.05) == 0.6
    assert calibrate_abstention.choose_threshold([-inf, -inf] + [0.6] * 18, 0.05) is None
    assert calibrate_abstention.choose_threshold([0.3] + [inf] * 19, 0.05) == 1.0


def test_calibrate_writes_an_applicable_value(tmp_path):
    records = [
        {"id": f"a{i}", "expected_behavior": "answer", "gate": "score", "gate_score": 0.5 + i / 100}
        for i in range(20)
    ]
    records += [
        {"id": f"u{i}", "expected_behavior": "abstain", "gate": "score", "gate_score": 0.4 + i / 10}
        for i in range(4)
    ]
    records.append({"id": "none", "expected_behavior": "answer"})  # an older run, no gate data
    records.append({"id": "ref", "expected_behavior": "abstain", "gate": "always_pass"})
    run_dir = write_run(tmp_path / "results" / "r1", {"mode": "retrieval"}, records)
    out = tmp_path / "abstention.json"
    code = calibrate_abstention.main(
        ["--results-dir", str(tmp_path / "results"), "--out", str(out)]
    )
    assert code == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["threshold"] == 0.51 and result["apply"] == "ABSTAIN_MIN_SIMILARITY=0.51"
    assert result["false_refusal_rate"] == 0.05
    assert result["unanswerable_pass_rate"] == 0.6  # 0.6, 0.7 and the Bekker reference
    assert result["n_always_pass"] == 1
    assert (result["n_answerable"], result["n_unanswerable"], result["n_without_score"]) == (
        20,
        5,
        1,
    )
    assert result["run_id"] == run_dir.name
    assert any(row["threshold"] == 0.51 for row in result["grid"])


def test_calibration_refuses_test_runs(tmp_path):
    run_dir = write_run(tmp_path / "r", {"mode": "retrieval", "split": "test"}, [])
    assert calibrate_abstention.main(["--run", str(run_dir), "--out", str(tmp_path / "o")]) == 1


# --- judge sample -----------------------------------------------------------------------------


def judged_record(i: int, *, fail: bool, cross: bool) -> dict:
    return {
        "id": f"q{i:02d}",
        "question_lang": "es",
        "expected_behavior": "answer",
        "answer": f"respuesta {i}",
        "answer_lang": "es",
        "strict_pass": not fail,
        "context": [
            {
                "rank": 1,
                "chunk_id": f"c{i}",
                "work_id": "eth-nic",
                "edition_id": "e",
                "lang": "en" if cross else "es",
                "bekker": "1106b",
            }
        ],
        "judge": {
            "faithfulness": {"verdict": "fail" if fail else "pass", "source": "judge"},
            "key_points": {"verdict": "pass", "source": "judge"},
        },
    }


def test_sample_is_stratified_and_deterministic():
    records = [judged_record(i, fail=i < 20, cross=i % 4 == 0) for i in range(80)]
    records.append({**judged_record(99, fail=True, cross=True), "judge": {}})  # not judged
    chosen = judge_sample.sample(records, n=50, min_fail=15, min_cross=10, seed=1)
    assert len(chosen) == 50 and len({r["id"] for r in chosen}) == 50
    assert sum(judge_sample.is_failure(r) for r in chosen) >= 15
    assert sum(judge_sample.is_cross_language(r) for r in chosen) >= 10
    assert all(r["id"] != "q99" for r in chosen)
    again = judge_sample.sample(records, n=50, min_fail=15, min_cross=10, seed=1)
    assert [r["id"] for r in again] == [r["id"] for r in chosen]


def test_sample_takes_what_there_is():
    records = [judged_record(i, fail=i < 3, cross=False) for i in range(10)]
    chosen = judge_sample.sample(records, n=50, min_fail=15, min_cross=10, seed=1)
    assert len(chosen) == 10


def test_pending_rows_hide_the_verdicts(tmp_path):
    run = RunData(tmp_path, {"run_id": "r1"}, [])
    rows = judge_sample.pending_rows(
        run, [judged_record(1, fail=True, cross=True)], {"q01": ["a|b", "c"]}
    )
    row = rows[0]
    assert row["labels"] == {"faithfulness": None, "key_points": None}
    assert row["key_points"] == ["a|b", "c"] and row["run_id"] == "r1"
    assert "judge" not in row and "strict_pass" not in row
    assert "fail" not in json.dumps(row)


# --- judge agreement --------------------------------------------------------------------------


def agreement_setup(tmp_path: Path, human: list[str], judge: list[str]) -> list[str]:
    records = [
        {"id": f"q{i}", "judge": {"faithfulness": {"verdict": j, "source": "judge"}}}
        for i, j in enumerate(judge)
    ]
    write_run(
        tmp_path / "results" / "r1",
        {"mode": "full", "models": {"judge": {"model": "jm", "prompts": {"f": "v1"}}}},
        records,
    )
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        "".join(
            json.dumps(
                {
                    "sample_id": f"s{i}",
                    "run_id": "r1",
                    "case_id": f"q{i}",
                    "labels": {"faithfulness": h, "key_points": None},
                }
            )
            + "\n"
            for i, h in enumerate(human)
        ),
        encoding="utf-8",
    )
    return [
        "--labels",
        str(labels),
        "--results-dir",
        str(tmp_path / "results"),
        "--out",
        str(tmp_path / "agreement.json"),
    ]


def test_agreement_passes(tmp_path, capsys):
    human = ["pass"] * 15 + ["fail"] * 5
    judge = ["pass"] * 15 + ["fail"] * 4 + ["pass"]  # 19/20 = 95 %
    assert judge_agreement.main(agreement_setup(tmp_path, human, judge)) == 0
    result = json.loads((tmp_path / "agreement.json").read_text(encoding="utf-8"))
    f = result["criteria"]["faithfulness"]
    assert f["n"] == 20 and f["agreement"] == 0.95
    assert f["fail_hit_rate"] == 0.8 and f["human_fails"] == 5
    assert f["kappa"] == pytest.approx(0.8571, abs=1e-4)
    assert result["passed"] and result["judge_model"] == "jm"
    assert "key_points" not in result["criteria"]  # unlabelled criteria are skipped
    assert "| faithfulness | 20 | 95,0 %" in capsys.readouterr().out


def test_agreement_below_minimum_fails(tmp_path):
    human = ["pass"] * 10 + ["fail"] * 10
    judge = ["pass"] * 10 + ["unknown"] * 4 + ["fail"] * 6  # unknown is a disagreement
    assert judge_agreement.main(agreement_setup(tmp_path, human, judge)) == 1
    result = json.loads((tmp_path / "agreement.json").read_text(encoding="utf-8"))
    assert result["criteria"]["faithfulness"]["agreement"] == 0.8
    assert result["criteria"]["faithfulness"]["judge_unknown"] == 4
    assert not result["passed"]


def test_agreement_without_labels(tmp_path):
    assert judge_agreement.main(["--labels", str(tmp_path / "missing.jsonl")]) == 1


def gate_records() -> list[dict]:
    return [
        {"id": f"a{i}", "expected_behavior": "answer", "gate": "score", "gate_score": 0.5 + i / 100}
        for i in range(20)
    ]


@pytest.mark.parametrize(
    "bad_meta",
    [
        {"dataset": {"version": "v", "includes_unreviewed": True}},
        {"limit": 5},
        {"ids_file": "evals/pr_subset.txt"},
        {"models": {"embedding": {"model": "fake/hash-embedder-v1", "provider": "fake"}}},
    ],
)
def test_calibration_uses_only_complete_reviewed_runs_with_real_embeddings(tmp_path, bad_meta):
    results = tmp_path / "results"
    good = write_run(results / "20261101-000000_a", {"mode": "retrieval"}, gate_records())
    bad = write_run(results / "20261102-000000_b", {"mode": "retrieval", **bad_meta}, [])
    out = tmp_path / "abstention.json"
    # The latest run only tests the code: the default skips it...
    assert calibrate_abstention.main(["--results-dir", str(results), "--out", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["run_id"] == good.name
    assert result["includes_unreviewed"] is False and result["run_problems"] == []
    assert result["limit"] is None and result["ids_file"] is None
    # ...and naming it explicitly is refused.
    assert calibrate_abstention.main(["--run", str(bad), "--out", str(tmp_path / "o")]) == 1
    assert not (tmp_path / "o").exists()


def test_judge_sample_skips_and_refuses_fake_or_unreviewed_runs(tmp_path, capsys):
    results = tmp_path / "results"
    judge_meta = {"mode": "full", "models": {"judge": {"model": "jm", "prompts": {"f": "v1"}}}}
    fake_verdicts = [judged_record(i, fail=i < 5, cross=False) for i in range(10)]
    for r in fake_verdicts:  # the same answers, judged by the fake LLM
        r["judge"] = {c: {**v, "provider": "fake"} for c, v in r["judge"].items()}
    fake = write_run(results / "20261101-000000_fake", judge_meta, fake_verdicts)
    unreviewed = write_run(
        results / "20261102-000000_unrev",
        {**judge_meta, "dataset": {"version": "v", "includes_unreviewed": True}},
        [judged_record(i, fail=True, cross=False) for i in range(10)],
    )
    real = write_run(
        results / "20261103-000000_real",
        judge_meta,
        [judged_record(i, fail=i < 5, cross=False) for i in range(10)],
    )
    out = tmp_path / "pending.jsonl"
    common = ["--results-dir", str(results), "--dataset-dir", str(tmp_path), "--no-context"]
    assert judge_sample.main([*common, "--out", str(out)]) == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {row["run_id"] for row in rows} == {real.name}  # not the first (fake) run
    for bad in (fake, unreviewed):
        assert judge_sample.main([*common, "--run", str(bad), "--out", str(tmp_path / "x")]) == 1
    assert "juez falso" in capsys.readouterr().out
    assert not (tmp_path / "x").exists()
