"""Compare two eval runs on the same questions (PLAN, "Estadística" and Etapa 6).

    python -m evals.compare RUN_A RUN_B [--primary strict_pass] [--cluster] [--noise 0.03]

For every metric both runs have: the paired difference B - A on the questions both runs
scored, with a bootstrap 95 % interval (fixed seed; --cluster resamples clusters of
questions that share an evidence passage), how many questions B fixed and broke, and the
exact sign-test p-value (McNemar's exact test for pass/fail metrics). Then the decision
rule of Etapa 6 on the primary metric, printed in Spanish:

- adopt if the metric improves and the interval of the difference excludes zero (and the
  difference is not below the run-to-run noise, when --noise is given);
- otherwise, adopt only if quality is neutral and cost or latency goes down;
- everything else is "sin diferencia detectable".

No decision (exit code 1) when either run includes unreviewed cases, is partial or used a
fake model (evals.report.run_problems), or when the primary metric needs the judge and a
run has none.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.settings import get_settings
from evals import stats
from evals.report import RunData, load_run, pct, run_problems

# Per-question value of each metric, from a per_question.jsonl record (None: not scored).
Extractor = Callable[[dict], float | None]


def _field(name: str) -> Extractor:
    def get(r: dict) -> float | None:
        value = r.get(name)
        return None if value is None else float(value)

    return get


def _judge(criterion: str) -> Extractor:
    def get(r: dict) -> float | None:
        verdict = (r.get("judge") or {}).get(criterion)
        return None if verdict is None else float(verdict["verdict"] == "pass")

    return get


METRICS: dict[str, Extractor] = {
    "recall_at_k": _field("recall_at_k"),
    "recall_at_candidates": _field("recall_at_candidates"),
    "all_evidence_at_k": _field("all_evidence_at_k"),
    "rr_at_10": _field("rr_at_10"),
    "strict_pass": _field("strict_pass"),
    "behavior_ok": _field("behavior_ok"),
    "faithfulness": _judge("faithfulness"),
    "key_points": _judge("key_points"),
    "premise_corrected": _judge("premise_corrected"),
    "citations_in_context": _field("citations_in_context"),
    "citation_recall": _field("citation_recall"),
    "lang_ok": _field("lang_ok"),
}
DEFAULT_PRIMARY = {"retrieval": "recall_at_k", "full": "strict_pass", "baseline": "behavior_ok"}
# Metrics defined only when a judge ran (strict_pass needs its verdicts too).
JUDGE_METRICS = {"strict_pass", "faithfulness", "key_points", "premise_corrected"}


def judged(run: RunData) -> bool:
    return bool((run.meta.get("summary") or {}).get("judged"))


def blockers(a: RunData, b: RunData, primary: str) -> list[str]:
    """Reasons not to decide on these runs (empty when a decision may be taken)."""
    out = [f"{run.path.name}: {p}" for run in (a, b) for p in run_problems(run)]
    if primary in JUDGE_METRICS and not (judged(a) and judged(b)):
        out.append(
            f"{primary} necesita el juez en las dos corridas (una se corrió con --no-judge); "
            "elegir otra con --primary, p. ej. behavior_ok"
        )
    return out


@dataclass
class MetricComparison:
    metric: str
    mean_a: float
    mean_b: float
    diff: stats.PairedDiff
    flips: stats.Flips
    p_value: float


def compare_metric(
    a: RunData,
    b: RunData,
    metric: str,
    *,
    resamples: int,
    seed: int,
    cluster: bool = False,
) -> MetricComparison | None:
    extract = METRICS[metric]
    by_b = b.by_id
    xa, xb, clusters = [], [], []
    for record in a.records:
        other = by_b.get(record["id"])
        if other is None:
            continue
        va, vb = extract(record), extract(other)
        if va is None or vb is None:
            continue
        xa.append(va)
        xb.append(vb)
        clusters.append(record.get("cluster") or record["id"])
    if not xa:
        return None
    diff = stats.paired_bootstrap(
        xa, xb, resamples=resamples, seed=seed, clusters=clusters if cluster else None
    )
    flips = stats.flips(xa, xb)
    return MetricComparison(
        metric=metric,
        mean_a=sum(xa) / len(xa),
        mean_b=sum(xb) / len(xb),
        diff=diff,
        flips=flips,
        p_value=stats.sign_test_p(flips.fixed, flips.broken),
    )


def points(x: float) -> str:
    return f"{100 * x:+.1f} pts".replace(".", ",")


def _usage(run: RunData) -> dict:
    return run.meta.get("summary", {}).get("usage") or {}


def decision(primary: MetricComparison | None, a: RunData, b: RunData, noise: float | None) -> str:
    """The Etapa 6 rule, in Spanish."""
    if primary is None:
        return "Sin datos pareados para la métrica principal: no se puede decidir."
    d = primary.diff
    name = primary.metric
    interval = f"{points(d.diff)} (IC 95 % {points(d.lo)} a {points(d.hi)}, n = {d.n})"
    if d.lo > 0:
        if noise is not None and d.diff < noise:
            return (
                f"Mejora {name} en {interval}, pero es menor que el ruido entre corridas "
                f"({points(noise)}): se ignora. Anotar como sin diferencia detectable."
            )
        return f"ADOPTAR: mejora {name} en {interval} y el intervalo excluye el cero."
    if d.hi < 0:
        return f"NO ADOPTAR: empeora {name} en {interval}."
    ua, ub = _usage(a), _usage(b)
    cheaper = [
        label
        for key, label in (
            ("usd_per_1000_queries", "costo"),
            ("latency_p50_ms", "latencia p50"),
            ("latency_p95_ms", "latencia p95"),
        )
        if ua.get(key) is not None and ub.get(key) is not None and ub[key] < ua[key]
    ]
    if cheaper:
        return (
            f"Neutro en {name} ({interval}) y baja {', '.join(cheaper)}: se puede adoptar "
            "por la regla de costo o latencia."
        )
    return f"Sin diferencia detectable en {name}: {interval}. No se adopta."


def render(
    a: RunData,
    b: RunData,
    comparisons: list[MetricComparison],
    verdict: str,
    warnings: list[str],
    cluster: bool,
) -> str:
    lines = [
        f"A: {a.path.name} ({a.meta['mode']}, {a.meta['split']})",
        f"B: {b.path.name} ({b.meta['mode']}, {b.meta['split']})",
        "Diferencia B - A sobre las mismas preguntas; IC 95 % por bootstrap pareado"
        + (" por clusters de pasaje" if cluster else "")
        + "; p exacto (signo / McNemar).",
        "",
        "| Métrica | A | B | Diferencia | IC 95 % | Arregladas | Rotas | p | n |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in comparisons:
        lines.append(
            f"| {c.metric} | {pct(c.mean_a)} | {pct(c.mean_b)} | {points(c.diff.diff)} | "
            f"{points(c.diff.lo)} a {points(c.diff.hi)} | {c.flips.fixed} | {c.flips.broken} | "
            f"{c.p_value:.3f} | {c.diff.n} |"
        )
    lines += ["", *[f"AVISO: {w}" for w in warnings], "", f"Decisión: {verdict}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compara dos corridas de evals.")
    parser.add_argument("run_a", type=Path)
    parser.add_argument("run_b", type=Path)
    parser.add_argument("--primary", choices=sorted(METRICS), default=None)
    parser.add_argument("--cluster", action="store_true", help="bootstrap por pasaje")
    parser.add_argument(
        "--noise", type=float, default=None, help="ruido entre corridas (fracción, ej. 0.03)"
    )
    args = parser.parse_args(argv)
    s = get_settings()

    a, b = load_run(args.run_a), load_run(args.run_b)
    if a.meta["split"] != b.meta["split"]:
        print(
            f"ERROR las corridas son de conjuntos distintos ({a.meta['split']} y "
            f"{b.meta['split']}): no se comparan."
        )
        return 1
    warnings = []
    if a.meta["dataset"]["version"] != b.meta["dataset"]["version"]:
        warnings.append(
            "el dataset cambió entre las corridas; solo se comparan las preguntas comunes"
        )
    unpaired = len(set(a.by_id) ^ set(b.by_id))
    if unpaired:
        warnings.append(f"{unpaired} preguntas están en una sola de las corridas")
    for run in (a, b):
        if run.meta.get("git", {}).get("dirty"):
            warnings.append(f"{run.path.name} se corrió con cambios sin commit")
    comparisons = [
        c
        for metric in METRICS
        if (
            c := compare_metric(
                a,
                b,
                metric,
                resamples=s.bootstrap_resamples,
                seed=s.bootstrap_seed,
                cluster=args.cluster,
            )
        )
        is not None
    ]
    primary_name = args.primary or DEFAULT_PRIMARY.get(b.meta["mode"], "recall_at_k")
    blocked = blockers(a, b, primary_name)
    if blocked:
        warnings += blocked
        verdict = "no se decide con estas corridas (ver los avisos)."
    else:
        primary = next((c for c in comparisons if c.metric == primary_name), None)
        verdict = decision(primary, a, b, args.noise)
    print(render(a, b, comparisons, verdict, warnings, args.cluster))
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
