"""Eval results on disk and their Markdown (PLAN, "Etapa 12: README y cierre").

- `load_run` / `list_runs`: read the run directories written by evals.run.
- `render_summary`: the summary.md of one run.
- `results_block`: the README results table (n, 95 % interval, date, model and corpus size
  for each run), placed between the markers below.

    python -m evals.report RUN_DIR [RUN_DIR ...] [--label NOMBRE ...]   # prints the block
    python -m evals.report RUN_DIR ... --write README.md                # replaces it
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
CALIBRATION_DIR = Path(__file__).resolve().parent / "calibration"
AGREEMENT_FILE = CALIBRATION_DIR / "agreement.json"
MARK_START = "<!-- evals:results:start -->"
MARK_END = "<!-- evals:results:end -->"


# --- runs on disk ----------------------------------------------------------------------------


@dataclass
class RunData:
    path: Path
    meta: dict
    records: list[dict]

    @property
    def by_id(self) -> dict[str, dict]:
        return {r["id"]: r for r in self.records}


def load_run(path: Path) -> RunData:
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    with (path / "per_question.jsonl").open(encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    return RunData(path, meta, records)


def list_runs(
    results_dir: Path = RESULTS_DIR,
    *,
    mode: str | None = None,
    split: str | None = None,
    usable_only: bool = False,
) -> list[Path]:
    """Run directories in chronological order (their names start with the timestamp).
    `usable_only`: only runs without run_problems(), the ones a decision may use."""
    out = []
    for path in sorted(p for p in results_dir.iterdir() if (p / "run.json").exists()):
        meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
        if (mode is None or meta.get("mode") == mode) and (
            split is None or meta.get("split") == split
        ):
            if usable_only and run_problems(load_run(path)):
                continue
            out.append(path)
    return out


def run_problems(run: RunData) -> list[str]:
    """Why a run cannot back a published figure, a calibration or a decision (empty when
    it can): it includes unreviewed cases, covers only part of the split, or used a fake
    embedder, LLM or judge. Such runs only test the code."""
    meta = run.meta
    problems = []
    if (meta.get("dataset") or {}).get("includes_unreviewed"):
        problems.append("incluye casos sin revisar")
    if meta.get("limit") is not None:
        problems.append(f"corrida parcial (--limit {meta['limit']})")
    if meta.get("ids_file"):
        problems.append(f"corrida parcial (--ids-file {meta['ids_file']})")
    models = meta.get("models") or {}
    if (models.get("embedding") or {}).get("provider") == "fake":
        problems.append("embeddings falsos")
    answered_by = (models.get("generator") or {}).get("answered_by") or []
    if any(a.get("provider") == "fake" for a in answered_by):
        problems.append("LLM falso")
    verdicts = [v for r in run.records for v in (r.get("judge") or {}).values()]
    if any(v.get("provider") == "fake" for v in verdicts):
        problems.append("juez falso")
    return problems


# --- summary.md ------------------------------------------------------------------------------

METRIC_LABELS = {
    "recall_at_k": "Recall de evidencia @{k}",
    "recall_at_candidates": "Recall de evidencia @{candidates} (diagnóstico)",
    "all_evidence_at_k": "Toda la evidencia @{k}",
    "all_evidence_at_candidates": "Toda la evidencia @{candidates}",
    "rr_at_10": "MRR@10 (casos de un grupo)",
    "mrr_at_10": "MRR@10 (casos de un grupo; sobre el contexto)",
    "strict_pass": "Acierto estricto de punta a punta",
    "faithfulness": "Fidelidad (juez)",
    "faithfulness_vs_gold": "Fidelidad respecto de los pasajes esperados (juez)",
    "false_answer_rate": "Respuestas dadas sin respuesta en el corpus",
    "false_refusal_rate": "Rechazos con respuesta en el corpus",
    "behavior_ok": "Comportamiento esperado (reglas)",
    "key_points": "Datos clave (juez)",
    "premise_corrected": "Premisa corregida (juez)",
    "has_citation": "Respuestas con al menos una cita",
    "citations_valid": "Respuestas con todas las citas válidas",
    "citations_in_context": "Respuestas sin citas fuera del contexto",
    "citation_precision": "Precisión de citas contra la evidencia",
    "citation_recall": "Recall de citas contra la evidencia",
    "lang_ok": "Respuesta en el idioma de la pregunta",
    "key_points_lexical": "Datos clave literales (diagnóstico)",
    "mentions_bekker": "Respuestas que mencionan una referencia Bekker",
    "bekker_ref_precision": "Referencias Bekker mencionadas que caen en la evidencia",
}


BREAKDOWN_LABELS = {"type": "tipo", "question_lang": "idioma de la pregunta", "work": "obra"}


def pct(x: float | None) -> str:
    if x is None:
        return "n/d"
    return f"{100 * x:.1f} %".replace(".", ",")


def metric_row(label: str, est: dict, *, percent: bool = True) -> str:
    fmt = pct if percent else (lambda v: "n/d" if v is None else f"{v:.3f}".replace(".", ","))
    if est["n"] == 0:
        return f"| {label} | n/d | n/d | 0 |"
    return f"| {label} | {fmt(est['value'])} | {fmt(est['lo'])} – {fmt(est['hi'])} | {est['n']} |"


def metrics_table(metrics: dict, fmt_args: dict) -> list[str]:
    lines = ["| Métrica | Valor | IC 95 % | n |", "|---|---|---|---|"]
    for key, est in metrics.items():
        label = METRIC_LABELS.get(key, key).format(**fmt_args)
        lines.append(metric_row(label, est, percent=not key.startswith(("rr_", "mrr_"))))
    return lines


def render_summary(meta: dict, summary: dict) -> str:
    s = meta["settings"]
    ds = meta["dataset"]
    emb = meta["models"].get("embedding") or {}
    fmt_args = {"k": s["retrieval_k"], "candidates": s["retrieval_candidates"]}
    title = {
        "retrieval": "Evals de retrieval",
        "full": "Evals completos",
        "baseline": "Línea base sin retrieval",
    }[meta["mode"]]
    if ds.get("includes_unreviewed"):
        title += " (INCLUYE CASOS SIN REVISAR)"
        dataset_line = (
            f"- Dataset `{ds['version']}`: {ds['n_split']} casos en {meta['split']}, de los "
            f"cuales {ds['n_unreviewed_included']} están SIN REVISAR."
        )
    else:
        dataset_line = (
            f"- Dataset `{ds['version']}`: {ds['n_split']} casos revisados en {meta['split']}, "
            f"ignorados por no tener revisado_por: {ds['n_ignored_unreviewed']}."
        )
    lines = [
        f"# {title} — {meta['split']}",
        "",
        f"- Corrida `{meta['run_id']}` ({meta['created_at']}), commit "
        f"`{(meta['git']['sha'] or 'sin git')[:7]}`"
        + (" con cambios sin commit" if meta["git"].get("dirty") else ""),
        dataset_line,
        f"- Corpus `{meta['corpus_version']}`, índice `{meta['index_version'] or 'n/d'}`, "
        f"embeddings `{emb.get('model', 'n/d')}` ({emb.get('provider', 'n/d')}).",
        f"- IC 95 %: Wilson para proporciones; bootstrap percentil para medias "
        f"({s['bootstrap_resamples']} remuestreos, semilla {s['bootstrap_seed']}).",
        f"- Acierto de un fragmento: solapamiento de {s['eval_overlap_min_lines']} líneas Bekker "
        f"con tolerancia de {s['eval_overlap_tolerance_lines']}.",
    ]
    for warning in meta.get("warnings", []):
        lines.append(f"- **AVISO**: {warning}")
    if meta["mode"] == "retrieval":
        lines.append(
            f"- {summary['n_scored']} de {summary['n_cases']} casos tienen evidencia y esperan "
            "respuesta; el resto (sin respuesta) solo aporta la similitud para calibrar."
        )
    else:
        gen = meta["models"].get("generator") or {}
        judge = meta["models"].get("judge") or {}
        lines.append(
            f"- Generador `{gen.get('model', 'n/d')}`; respondió: "
            + (
                ", ".join(
                    f"`{a['model']}` ({a['provider']}, {a['n']})"
                    for a in gen.get("answered_by", [])
                )
                or "n/d"
            )
        )
        lines.append(
            f"- Juez `{judge.get('model') or 'sin juez'}`"
            + (f", prompts {', '.join(judge.get('prompts', {}).values())}" if judge else "")
        )
        lines.append(
            f"- {summary['n_answered']} respondidas, {summary['n_abstained']} abstenciones, "
            f"{summary.get('n_failed', 0)} errores del LLM (cuentan como fallo)."
        )
    lines += ["", "## Métricas", "", *metrics_table(summary["metrics"], fmt_args)]
    if summary.get("judge_counts"):
        lines += [
            "",
            "## Veredictos del juez",
            "",
            "| Criterio | pass | fail | unknown | no interpretables | por regla |",
            "|---|---|---|---|---|---|",
        ]
        for criterion, c in summary["judge_counts"].items():
            lines.append(
                f"| {criterion} | {c.get('pass', 0)} | {c.get('fail', 0)} | "
                f"{c.get('unknown', 0)} | {c.get('parse_errors', 0)} | {c.get('by_rule', 0)} |"
            )
    if "usage" in summary:
        u = summary["usage"]
        p50 = "n/d" if u["latency_p50_ms"] is None else f"{u['latency_p50_ms']:.0f} ms"
        p95 = "n/d" if u["latency_p95_ms"] is None else f"{u['latency_p95_ms']:.0f} ms"
        lines += [
            "",
            "## Uso",
            "",
            f"- Latencia p50 {p50}, p95 {p95} (n = {u['latency_n']} sin caché).",
            f"- Tokens por consulta: {u['tokens_in_mean']} de entrada, {u['tokens_out_mean']} "
            f"de salida; {u['usd_per_1000_queries']} USD por 1.000 consultas a precios de "
            "settings (a confirmar).",
        ]
    for name, table in summary.get("by", {}).items():
        if not table:
            continue
        title = BREAKDOWN_LABELS.get(name, name)
        metric_keys = list(next(iter(table.values())))
        headers = [METRIC_LABELS.get(m, m).format(**fmt_args) for m in metric_keys]
        lines += ["", f"## Por {title}", "", f"| {title} | " + " | ".join(headers) + " |"]
        lines.append("|---" * (len(metric_keys) + 1) + "|")
        for label, row in table.items():
            cells = [
                f"{pct(row[m]['value'])} (n = {row[m]['n']})" if row[m]["n"] else "n/d"
                for m in metric_keys
            ]
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# --- README block ----------------------------------------------------------------------------

# Columns of the README table: metric key in run.json's summary, and header.
README_COLUMNS = (
    ("recall_at_k", "Recall de evidencia @k"),
    ("strict_pass", "Acierto estricto"),
    ("faithfulness", "Fidelidad"),
    ("false_answer_rate", "Responde sin respuesta"),
    ("false_refusal_rate", "Rechaza con respuesta"),
)
# Columns that depend on the judge's verdicts: published only with a passing calibration
# of the same judge model and prompts (PLAN, "Antes de publicar una métrica con juez").
JUDGE_COLUMNS = {"strict_pass", "faithfulness"}
UNCALIBRATED_CELL = "no publicada (juez sin calibrar)"
SYSTEM_NAMES = {
    "retrieval": "Retrieval",
    "full": "RAG",
    "baseline": "Sin retrieval",
}


def cell(est: dict | None) -> str:
    if not est or not est.get("n"):
        return "—"
    return f"{pct(est['value'])} ({pct(est['lo'])}–{pct(est['hi'])}; n = {est['n']})"


def _model(meta: dict) -> str:
    gen = (meta.get("models") or {}).get("generator") or {}
    answered = gen.get("answered_by") or []
    if answered:
        return ", ".join(f"{a['model']} ({a['provider']})" for a in answered)
    emb = (meta.get("models") or {}).get("embedding") or {}
    return f"embeddings {emb.get('model', 'n/d')}"


def _judge_key(judge: dict) -> tuple:
    return judge.get("model"), tuple(sorted((judge.get("prompts") or {}).values()))


def judge_calibrated(meta: dict, agreement: dict | None) -> bool:
    """Whether the run's judge has a passing calibration (same model and prompts)."""
    judge = (meta.get("models") or {}).get("judge")
    if not judge or not agreement or not agreement.get("passed"):
        return False
    calibrated = {"model": agreement.get("judge_model"), "prompts": agreement.get("prompts")}
    return _judge_key(calibrated) == _judge_key(judge)


def results_block(
    runs: list[RunData], labels: list[str] | None = None, agreement: dict | None = None
) -> str:
    """Markdown table with one row per run; metrics with their 95 % interval and n. The
    judge-based cells of a run whose judge is not calibrated are withheld."""
    header = [
        "Sistema",
        "Conjunto",
        *[name for _, name in README_COLUMNS],
        "Fecha",
        "Modelo",
        "Corpus",
    ]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    judges = set()
    for i, run in enumerate(runs):
        meta = run.meta
        metrics = meta["summary"]["metrics"]
        if meta["mode"] == "baseline":
            metrics = {**metrics, "faithfulness": metrics.get("faithfulness_vs_gold")}
        label = labels[i] if labels and i < len(labels) else SYSTEM_NAMES.get(meta["mode"], "")
        units = meta.get("corpus_units")
        corpus = f"{units} unidades, corpus {meta['corpus_version']}" if units else "n/d"
        calibrated = judge_calibrated(meta, agreement)
        cells = []
        for key, _ in README_COLUMNS:
            value = cell(metrics.get(key))
            if key in JUDGE_COLUMNS and value != "—" and not calibrated:
                value = UNCALIBRATED_CELL
            cells.append(value)
        row = [
            label,
            meta["split"],
            *cells,
            meta["created_at"][:10],
            _model(meta),
            corpus,
        ]
        lines.append("| " + " | ".join(row) + " |")
        judge = (meta.get("models") or {}).get("judge")
        if judge:
            judges.add((judge["model"], tuple(sorted(judge.get("prompts", {}).values()))))
    lines += [
        "",
        "Cada cifra lleva su intervalo del 95 % y n. Con estos tamaños, una diferencia de 3 "
        "puntos no se distingue del ruido. La fidelidad de la línea base sin retrieval se "
        "mide contra los pasajes esperados, no contra un contexto recuperado.",
    ]
    for i, run in enumerate(runs):
        for warning in run.meta.get("warnings", []):
            label = labels[i] if labels and i < len(labels) else run.path.name
            lines += ["", f"**Atención ({label})**: {warning}."]
    if judges:
        lines.append("")
        lines.append(agreement_note(agreement, judges))
    return "\n".join(lines) + "\n"


def agreement_note(agreement: dict | None, judges: set) -> str:
    if not agreement:
        return (
            "Métricas con juez sin calibrar: falta `python -m evals.judge_agreement` contra "
            "etiquetas humanas."
        )
    per = ", ".join(
        f"{c} {pct(v['agreement'])} (n = {v['n']})" for c, v in agreement["criteria"].items()
    )
    note = f"Acuerdo del juez con etiquetas humanas ciegas: {per}."
    calibrated = (
        agreement.get("judge_model"),
        tuple(sorted(agreement.get("prompts", {}).values())),
    )
    if calibrated not in judges or len(judges) > 1:
        note += " Atención: la calibración es de otro modelo o prompt del juez; repetirla."
    if not agreement.get("passed"):
        note += " No llega al mínimo: las métricas con juez no se publican."
    return note


def replace_block(text: str, block: str) -> str:
    start, end = text.find(MARK_START), text.find(MARK_END)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no encuentro los marcadores {MARK_START} y {MARK_END}")
    return text[: start + len(MARK_START)] + "\n" + block + text[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tabla de resultados para el README.")
    parser.add_argument("runs", nargs="+", type=Path, help="directorios de corridas")
    parser.add_argument("--label", action="append", default=None, help="nombre de cada fila")
    parser.add_argument("--write", type=Path, default=None, help="README a actualizar")
    parser.add_argument("--agreement", type=Path, default=AGREEMENT_FILE)
    args = parser.parse_args(argv)

    runs = [load_run(path) for path in args.runs]
    agreement = None
    if args.agreement.exists():
        agreement = json.loads(args.agreement.read_text(encoding="utf-8"))
    block = results_block(runs, args.label, agreement)
    unusable = [(run, problems) for run in runs if (problems := run_problems(run))]
    if args.write is not None and unusable:
        for run, problems in unusable:
            print(f"ERROR {run.path.name}: {'; '.join(problems)}.")
        print(f"No se escribe {args.write}: esas corridas solo prueban el código.")
        return 1
    if args.write is None:
        print(MARK_START)
        print(block, end="")
        print(MARK_END)
        return 0
    try:
        updated = replace_block(args.write.read_text(encoding="utf-8"), block)
    except ValueError as exc:
        print(f"ERROR {args.write}: {exc}. Agregarlos donde va la tabla y volver a correr.")
        return 1
    args.write.write_text(updated, encoding="utf-8")
    print(f"Tabla de resultados actualizada en {args.write}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
