"""Sample answers for the blind human calibration of the judge (PLAN, Etapa 5).

    python -m evals.judge_sample --n 50 [--run DIR] [--out evals/calibration/pending.jsonl]

Takes answers the judge evaluated in a full run on dev (by default the first complete one
with reviewed cases and real models; any other run is refused), with at
least eval_judge_sample_min_fail (15) failures and eval_judge_sample_min_cross_lang (10)
answers whose context is in another language, when the run has them; the rest at random
(fixed seed). Writes them WITHOUT the judge's verdicts to pending.jsonl. The person copies
it to labels.jsonl, fills `labels` with "pass" or "fail" per criterion, and runs
`python -m evals.judge_agreement`.

pending.jsonl holds ids, questions, answers and Bekker ranges, never corpus text. The
passages themselves, to read while labelling, go to evals/.cache/calibration/ (ignored by
git), fetched from the index when the database is reachable.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from app.settings import get_settings
from evals.report import CALIBRATION_DIR, RESULTS_DIR, RunData, list_runs, load_run, run_problems
from evals.schema import DATASET_DIR, load_dataset

OUT_FILE = CALIBRATION_DIR / "pending.jsonl"
CONTEXT_FILE = Path(__file__).resolve().parent / ".cache" / "calibration" / "pending_context.md"


def judged_criteria(record: dict) -> list[str]:
    """Criteria the judge itself evaluated (rule verdicts are not calibrated)."""
    return [c for c, v in (record.get("judge") or {}).items() if v.get("source") == "judge"]


def is_failure(record: dict) -> bool:
    return record.get("strict_pass") is not True


def is_cross_language(record: dict) -> bool:
    lang = record.get("answer_lang") or record.get("question_lang")
    return any(c.get("lang") != lang for c in record.get("context") or [])


def sample(records: list[dict], *, n: int, min_fail: int, min_cross: int, seed: int) -> list[dict]:
    """Stratified, deterministic sample of judged records."""
    rng = random.Random(seed)
    pool = [r for r in records if judged_criteria(r)]
    rng.shuffle(pool)
    chosen: list[dict] = []

    def take(candidates: list[dict], count: int) -> None:
        for r in candidates:
            if count <= 0 or len(chosen) >= n:
                return
            if r not in chosen:
                chosen.append(r)
                count -= 1

    take([r for r in pool if is_failure(r)], min_fail)
    have_cross = sum(is_cross_language(r) for r in chosen)
    take([r for r in pool if is_cross_language(r)], min_cross - have_cross)
    take(pool, n - len(chosen))
    rng.shuffle(chosen)  # blind: failures are not grouped first
    return chosen


def pending_rows(run: RunData, chosen: list[dict], key_points: dict[str, list[str]]) -> list[dict]:
    rows = []
    for i, r in enumerate(chosen, start=1):
        rows.append(
            {
                "sample_id": f"s{i:02d}",
                "run_id": run.meta["run_id"],
                "case_id": r["id"],
                "question_lang": r["question_lang"],
                "expected_behavior": r["expected_behavior"],
                "answer": r["answer"],
                "answer_lang": r.get("answer_lang"),
                "key_points": key_points.get(r["id"], []),
                "context": [
                    {
                        k: c.get(k)
                        for k in ("rank", "chunk_id", "work_id", "edition_id", "lang", "bekker")
                    }
                    for c in r.get("context") or []
                ],
                "criteria": judged_criteria(r),
                "labels": {c: None for c in judged_criteria(r)},
                "notes": "",
            }
        )
    return rows


def write_context_file(rows: list[dict], questions: dict[str, str], path: Path) -> bool:
    """Readable passages for the labeller (corpus text: kept out of git)."""
    try:
        from app.db import connect
        from app.retrieval import get_chunk

        conn = connect()
    except Exception as exc:  # no database here: the labeller fetches passages elsewhere
        print(f"AVISO sin base de datos ({exc}): no se escribe {path.name}.")
        return False
    lines = ["# Pasajes para etiquetar (no subir: texto del corpus)", ""]
    with conn:
        for row in rows:
            lines += [
                f"## {row['sample_id']} ({row['case_id']})",
                "",
                f"Pregunta: {questions.get(row['case_id'], '')}",
                "",
            ]
            for c in row["context"]:
                chunk = get_chunk(c["chunk_id"], conn=conn)
                text = chunk.text if chunk else "(fragmento no encontrado en el índice activo)"
                lines += [
                    f"[{c['rank']}] {c['work_id']} {c['bekker']} ({c['edition_id']})",
                    "",
                    text,
                    "",
                ]
            lines += [f"Respuesta: {row['answer']}", "", "---", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    parser = argparse.ArgumentParser(description="Muestra para calibrar el juez a ciegas.")
    parser.add_argument("--n", type=int, default=s.eval_judge_sample_n)
    parser.add_argument("--run", type=Path, default=None, help="corrida completa sobre dev")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--out", type=Path, default=OUT_FILE)
    parser.add_argument("--no-context", action="store_true", help="sin el archivo de pasajes")
    parser.add_argument("--context-file", type=Path, default=CONTEXT_FILE)
    args = parser.parse_args(argv)

    if args.run is None:
        runs = [
            p
            for p in list_runs(args.results_dir, mode="full", split="dev", usable_only=True)
            if load_run(p).meta["models"].get("judge")
        ]
        if not runs:
            print(
                "ERROR No hay corridas completas con juez sobre dev, con casos revisados y "
                "modelos reales: correr `make eval`."
            )
            return 1
        args.run = runs[0]  # the first full run on dev, as the plan says
    run = load_run(args.run)
    if run.meta["split"] != "dev":
        print("ERROR La calibración del juez usa respuestas de dev.")
        return 1
    problems = run_problems(run)
    if not run.meta["models"].get("judge"):
        problems.append("sin juez")
    if problems:
        print(f"ERROR {run.path.name} no sirve para calibrar el juez: {'; '.join(problems)}.")
        return 1
    chosen = sample(
        run.records,
        n=args.n,
        min_fail=s.eval_judge_sample_min_fail,
        min_cross=s.eval_judge_sample_min_cross_lang,
        seed=s.bootstrap_seed,
    )
    dataset = load_dataset(args.dataset_dir)
    key_points = {c.id: c.key_points for c in dataset.cases}
    questions = {c.id: c.question for c in dataset.cases}
    rows = pending_rows(run, chosen, key_points)
    for row in rows:
        row["question"] = questions.get(row["case_id"], "")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    fails = sum(is_failure(r) for r in chosen)
    cross = sum(is_cross_language(r) for r in chosen)
    print(
        f"{len(rows)} respuestas de {run.meta['run_id']} ({fails} fallidas, {cross} con contexto "
        f"en otro idioma) en {args.out}, sin los veredictos del juez."
    )
    if len(rows) < args.n or fails < s.eval_judge_sample_min_fail:
        print("AVISO la corrida no alcanza para la muestra pedida (n o fallidas).")
    if cross < s.eval_judge_sample_min_cross_lang:
        print("AVISO menos respuestas con contexto en otro idioma que las pedidas.")
    if not args.no_context and write_context_file(rows, questions, args.context_file):
        print(f"Pasajes para leer en {args.context_file} (no se sube: texto del corpus).")
    print(
        "Siguiente paso: copiar el archivo a evals/calibration/labels.jsonl, completar cada "
        '"labels" con "pass" o "fail" sin mirar los veredictos del juez, y correr '
        "`python -m evals.judge_agreement`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
