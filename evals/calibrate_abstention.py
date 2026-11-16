"""Calibrate ABSTAIN_MIN_SIMILARITY on dev (PLAN, Etapa 5).

    python -m evals.calibrate_abstention [--run DIR] [--out evals/calibration/abstention.json]

Reads, from a retrieval run (by default the latest complete `make eval-fast` on dev with
reviewed cases and real embeddings; any other run is refused), what the
backend's similarity gate sees for every dev question: the best dense score among the k
passages. The backend abstains without calling the LLM when that score is below
ABSTAIN_MIN_SIMILARITY; questions resolved by an explicit Bekker reference always pass,
and questions with no passage always abstain. Picks the highest threshold that rejects at
most eval_abstain_max_false_refusal (5 %) of the questions that have an answer.

It prints both error rates for a grid of thresholds and writes the chosen value to a file
the person can apply. It never edits settings: the person sets ABSTAIN_MIN_SIMILARITY in
.env or changes the default in app/settings.py.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

from app.settings import get_settings
from evals.report import (
    CALIBRATION_DIR,
    RESULTS_DIR,
    RunData,
    list_runs,
    load_run,
    pct,
    run_problems,
)

OUT_FILE = CALIBRATION_DIR / "abstention.json"
GRID = [round(0.05 * i, 2) for i in range(21)]


def choose_threshold(answerable_scores: list[float], max_false_refusal: float) -> float | None:
    """Highest t with share(score < t) <= max_false_refusal, rounded down to 4 decimals
    (rounding down never rejects more). Scores may be -inf (always rejected) or +inf
    (never rejected). None when no threshold meets the limit or there are no scores."""
    if not answerable_scores:
        return None
    ordered = sorted(answerable_scores)
    allowed = math.floor(max_false_refusal * len(ordered) + 1e-9)
    candidate = ordered[min(allowed, len(ordered) - 1)]
    if candidate == -math.inf:
        return None  # more questions without any passage than the allowed share
    if candidate == math.inf:
        candidate = 1.0  # the rest always pass: the highest similarity is the limit
    return math.floor(candidate * 1e4) / 1e4


def error_rates(
    answerable: list[float], unanswerable: list[float], threshold: float
) -> tuple[float | None, float | None]:
    """(answerable rejected, unanswerable that still reach the LLM) at `threshold`."""
    refused = sum(s < threshold for s in answerable) / len(answerable) if answerable else None
    passed = sum(s >= threshold for s in unanswerable) / len(unanswerable) if unanswerable else None
    return refused, passed


GATE_VALUES = {"always_pass": math.inf, "always_abstain": -math.inf}


def scores_from_run(run: RunData) -> tuple[list[float], list[float], int]:
    """(answerable scores, unanswerable scores, cases without gate data)."""
    answerable, unanswerable, missing = [], [], 0
    for r in run.records:
        gate = r.get("gate")
        if gate == "score" and r.get("gate_score") is not None:
            score = float(r["gate_score"])
        elif gate in GATE_VALUES:
            score = GATE_VALUES[gate]
        else:
            missing += 1
            continue
        (unanswerable if r["expected_behavior"] == "abstain" else answerable).append(score)
    return answerable, unanswerable, missing


def calibrate(run: RunData, max_false_refusal: float) -> dict:
    answerable, unanswerable, missing = scores_from_run(run)
    threshold = choose_threshold(answerable, max_false_refusal)
    grid = sorted(set(GRID) | ({threshold} if threshold is not None else set()))
    rows = []
    for t in grid:
        refused, passed = error_rates(answerable, unanswerable, t)
        rows.append(
            {"threshold": t, "false_refusal_rate": refused, "unanswerable_pass_rate": passed}
        )
    refused, passed = (
        error_rates(answerable, unanswerable, threshold) if threshold is not None else (None, None)
    )
    meta = run.meta
    return {
        "threshold": threshold,
        "apply": f"ABSTAIN_MIN_SIMILARITY={threshold}" if threshold is not None else None,
        "max_false_refusal": max_false_refusal,
        "false_refusal_rate": refused,
        "unanswerable_pass_rate": passed,
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "n_without_score": missing,
        "n_always_pass": sum(math.isinf(x) and x > 0 for x in answerable + unanswerable),
        "n_always_abstain": sum(math.isinf(x) and x < 0 for x in answerable + unanswerable),
        "run_id": meta["run_id"],
        "split": meta["split"],
        "index_version": meta.get("index_version"),
        "embedding": (meta.get("models") or {}).get("embedding"),
        "dataset_version": meta["dataset"]["version"],
        # What the run covered, so the file shows the value came from a complete run of
        # reviewed cases (main() refuses any other).
        "includes_unreviewed": bool(meta["dataset"].get("includes_unreviewed")),
        "limit": meta.get("limit"),
        "ids_file": meta.get("ids_file"),
        "run_problems": run_problems(run),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "grid": rows,
    }


def render(result: dict) -> str:
    lines = [
        f"Corrida {result['run_id']} ({result['split']}): {result['n_answerable']} preguntas con "
        f"respuesta y {result['n_unanswerable']} sin respuesta.",
        "",
        "| Umbral | Rechazos con respuesta | Sin respuesta que llegan al LLM |",
        "|---|---|---|",
    ]
    for row in result["grid"]:
        mark = " (elegido)" if row["threshold"] == result["threshold"] else ""
        lines.append(
            f"| {row['threshold']:.4f}{mark} | {pct(row['false_refusal_rate'])} | "
            f"{pct(row['unanswerable_pass_rate'])} |"
        )
    lines.append("")
    if result["threshold"] is None:
        lines.append("Sin preguntas con respuesta y similitud: no hay umbral que elegir.")
    else:
        lines.append(
            f"Umbral elegido: {result['threshold']} (rechaza {pct(result['false_refusal_rate'])} "
            f"de las preguntas con respuesta; máximo {pct(result['max_false_refusal'])})."
        )
        lines.append(
            f"Para aplicarlo: {result['apply']} en .env, o como valor por defecto en "
            "app/settings.py con el acuerdo de la persona."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibra el umbral de abstención sobre dev.")
    parser.add_argument("--run", type=Path, default=None, help="corrida de retrieval sobre dev")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--out", type=Path, default=OUT_FILE)
    args = parser.parse_args(argv)
    s = get_settings()

    if args.run is None:
        runs = list_runs(args.results_dir, mode="retrieval", split="dev", usable_only=True)
        if not runs:
            print(
                "ERROR No hay corridas de retrieval completas sobre dev, con casos revisados y "
                "embeddings reales: correr `make eval-fast` primero."
            )
            return 1
        args.run = runs[-1]
    run = load_run(args.run)
    if run.meta["split"] != "dev":
        print("ERROR El umbral se calibra solo sobre dev.")
        return 1
    problems = run_problems(run)
    if problems:
        print(f"ERROR {run.path.name} no sirve para calibrar: {'; '.join(problems)}.")
        return 1
    result = calibrate(run, s.eval_abstain_max_false_refusal)
    print(render(result))
    if result["threshold"] is None:
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Escrito en {args.out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
