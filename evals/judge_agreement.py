"""Agreement between the judge and the blind human labels (PLAN, "Reglas y juez").

    python -m evals.judge_agreement [--labels evals/calibration/labels.jsonl]

For each criterion: agreement, Cohen's kappa, and the share of human-labelled failures the
judge also failed. The judge's verdicts are read from the run each label row names. An
"unknown" verdict counts as a disagreement. Exits 1 when any criterion is below
eval_judge_min_agreement (85 %), or when there are no labels. Writes
evals/calibration/agreement.json (judge model, prompt versions, figures) for the README.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.settings import get_settings
from evals import stats
from evals.report import AGREEMENT_FILE, CALIBRATION_DIR, RESULTS_DIR, load_run, pct

LABELS_FILE = CALIBRATION_DIR / "labels.jsonl"


@dataclass
class CriterionAgreement:
    human: list[str] = field(default_factory=list)
    judge: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.human)

    @property
    def agreement(self) -> float | None:
        if not self.n:
            return None
        return sum(h == j for h, j in zip(self.human, self.judge, strict=True)) / self.n

    @property
    def kappa(self) -> float:
        return stats.cohen_kappa(self.human, self.judge)

    @property
    def human_fails(self) -> int:
        return self.human.count("fail")

    @property
    def fail_hit_rate(self) -> float | None:
        """Share of human-labelled failures the judge also failed."""
        if not self.human_fails:
            return None
        hits = sum(h == "fail" and j == "fail" for h, j in zip(self.human, self.judge, strict=True))
        return hits / self.human_fails

    def to_json(self) -> dict:
        kappa = self.kappa
        return {
            "n": self.n,
            "agreement": self.agreement,
            "kappa": None if kappa != kappa else round(kappa, 4),
            "human_fails": self.human_fails,
            "fail_hit_rate": self.fail_hit_rate,
            "judge_unknown": self.judge.count("unknown"),
        }


def collect(
    label_rows: list[dict], results_dir: Path
) -> tuple[dict[str, CriterionAgreement], list[str], dict]:
    """Pair each human label with the judge's verdict. Returns (per criterion, problems,
    judge info of the runs used)."""
    per: dict[str, CriterionAgreement] = {}
    problems: list[str] = []
    runs: dict[str, dict] = {}
    judge_info: dict = {}
    for row in label_rows:
        run_id = row["run_id"]
        if run_id not in runs:
            path = results_dir / run_id
            if not (path / "run.json").exists():
                problems.append(f"{row.get('sample_id')}: no encuentro la corrida {run_id}")
                runs[run_id] = {}
                continue
            run = load_run(path)
            runs[run_id] = run.by_id
            judge_info = run.meta["models"].get("judge") or {}
        record = runs[run_id].get(row["case_id"])
        if record is None:
            if runs[run_id]:
                problems.append(f"{row.get('sample_id')}: {row['case_id']} no está en {run_id}")
            continue
        for criterion, label in (row.get("labels") or {}).items():
            if label is None:
                continue
            if label not in ("pass", "fail"):
                problems.append(f"{row.get('sample_id')}: etiqueta inválida {label!r}")
                continue
            verdict = (record.get("judge") or {}).get(criterion)
            if verdict is None:
                problems.append(f"{row.get('sample_id')}: sin veredicto del juez para {criterion}")
                continue
            entry = per.setdefault(criterion, CriterionAgreement())
            entry.human.append(label)
            entry.judge.append(verdict["verdict"])
    return dict(sorted(per.items())), problems, judge_info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acuerdo del juez con las etiquetas humanas.")
    parser.add_argument("--labels", type=Path, default=LABELS_FILE)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--out", type=Path, default=AGREEMENT_FILE)
    args = parser.parse_args(argv)
    s = get_settings()

    if not args.labels.exists():
        print(f"ERROR falta {args.labels}: etiquetar la muestra de `python -m evals.judge_sample`.")
        return 1
    raw = args.labels.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    per, problems, judge_info = collect(rows, args.results_dir)
    for problem in problems:
        print(f"  AVISO {problem}")
    if not per:
        print("ERROR no hay etiquetas completas para comparar.")
        return 1

    minimum = s.eval_judge_min_agreement
    print("| Criterio | n | Acuerdo | Kappa de Cohen | Fallas humanas | Detectadas por el juez |")
    print("|---|---|---|---|---|---|")
    for criterion, entry in per.items():
        data = entry.to_json()
        kappa = "n/d" if data["kappa"] is None else f"{data['kappa']:.2f}"
        print(
            f"| {criterion} | {entry.n} | {pct(entry.agreement)} | {kappa} | "
            f"{entry.human_fails} | {pct(entry.fail_hit_rate)} |"
        )
    failing = [c for c, e in per.items() if (e.agreement or 0) < minimum]
    passed = not failing
    result = {
        "judge_model": judge_info.get("model"),
        "prompts": judge_info.get("prompts", {}),
        "labels_sha256": hashlib.sha256(raw).hexdigest(),
        "min_agreement": minimum,
        "passed": passed,
        "criteria": {c: e.to_json() for c, e in per.items()},
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if passed:
        print(f"Acuerdo de {pct(minimum)} o más en todos los criterios. Escrito en {args.out}.")
        return 0
    print(
        f"Acuerdo bajo el {pct(minimum)} en: {', '.join(failing)}. Corregir la rúbrica y repetir "
        "una vez; si sigue abajo, esas métricas no se publican."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
