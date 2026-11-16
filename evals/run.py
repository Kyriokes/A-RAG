"""Eval runner (PLAN, Etapas 3 y 5).

    python -m evals.run --mode retrieval --split dev   # make eval-fast: retrieval, no LLM
    python -m evals.run --mode full --split dev        # make eval: generation + judge
    python -m evals.run --mode baseline --split dev    # baseline without retrieval
    python -m evals.run --mode full --split test --confirm-test
    python -m evals.run --mode full --split dev --ids-file evals/pr_subset.txt --no-judge
    python -m evals.run --check-latest [--baseline-file main.json]   # CI step

Every run reads evals/dataset/*.jsonl, ignores cases without `revisado_por` (and says how
many), validates the labels against the catalogue and the processed corpus, and writes
evals/results/<YYYYmmdd-HHMMSS>_<gitsha7>_<confighash8>/ with run.json, per_question.jsonl
(ids, ranks, Bekker ranges, verdicts and the model's answers; never corpus text) and
summary.md. Retrieval runs on `dev` also write evals/results/retrieval_dev_latest.json,
which CI checks against the commit's configuration.

`test` is reserved for milestones: it needs --confirm-test, and every run on it is
counted in evals/results/test_runs.json.

`--include-unreviewed` (dev only) also runs the cases nobody has reviewed yet, to exercise
the code path before the review. Such a run is labelled in its output, run.json and
summary.md, and is never written as retrieval_dev_latest.json. Neither is a partial run
(`--limit`, `--ids-file`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import bekker
from app.models import TextType, Unit, Work
from app.query_parse import detect_lang
from app.settings import ROOT, Settings, get_settings
from evals import stats
from evals.cost import (
    CACHE_DIR,
    CachingLLM,
    CostLimitExceeded,
    QuotaExhausted,
    ResponseCache,
    estimate_cost,
    guard,
)
from evals.judge import (
    JudgeNotConfigured,
    Prompt,
    Verdict,
    criteria_for,
    load_prompt,
    max_judge_calls,
)
from evals.labels import evidence_clusters, unit_location, validate_labels
from evals.metrics import (
    Location,
    MatchParams,
    all_evidence_at,
    behavior_met,
    bekker_overlap_hit,
    check_citations,
    groups_hit_by,
    key_points_lexical,
    ranks_from_hits,
    recall_at,
    reciprocal_rank,
    span_hit,
    strict_pass,
)
from evals.report import RESULTS_DIR, pct, render_summary
from evals.schema import (
    DATASET_DIR,
    BekkerSpan,
    Case,
    ExpectedBehavior,
    LoadedDataset,
    load_dataset,
)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
LATEST_NAME = "retrieval_dev_latest.json"
TEST_COUNTER_NAME = "test_runs.json"
BASELINE_PROMPT = "baseline_no_retrieval_v1.md"
MODES = ("retrieval", "full", "baseline")
MRR_CUTOFF = 10
FAIL_FAST_CASES = 3  # a full run stops when its first answers all fail in the LLM

# What the config hash covers. Settings by prefix, so a new retrieval parameter
# (e.g. hybrid_*) is included without editing this list.
RETRIEVAL_SETTING_PREFIXES = (
    "retrieval_",
    "chunk_",
    "dedup_",
    "bekker_",
    "eval_overlap_",
    "bootstrap_",
    "hybrid_",
    "rerank_",
)
RETRIEVAL_SETTING_NAMES = (
    "embedding_model",
    "embedding_dim",
    "embedding_normalize",
    "chars_per_token",
)
GENERATION_SETTING_NAMES = (
    "llm_model",
    "llm_reasoning_effort",
    "context_max_tokens",
    "output_max_tokens",
    "abstain_min_similarity",
    "judge_model",
    "eval_judge_max_tokens",
)
RETRIEVAL_CODE = (
    "app/bekker.py",
    "app/chunking.py",
    "app/embeddings.py",
    "app/index.py",
    "app/query_parse.py",
    "app/retrieval.py",
    "app/corpus/**/*.py",
    "corpus/*.yaml",
    "evals/metrics.py",
)
GENERATION_CODE = (
    "app/generation*.py",
    "app/citations.py",
    "app/llm.py",
    "app/prompts/**/*",
    "evals/judge.py",
    "evals/prompts/*.md",
)

Retriever = Callable[[str], list[Any]]  # question -> list[RetrievedChunk], rank order
Answerer = Callable[[str], Any]  # question -> AskResult


class RunError(RuntimeError):
    """A problem the person has to fix; the message says what. Exit code 1."""


# --- configuration, versions and environment ---------------------------------------------


def _hash_files(patterns: Sequence[str], root: Path) -> dict[str, str]:
    out = {}
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                data = path.read_bytes().replace(b"\r\n", b"\n")
                out[path.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
    return out


def config_payload(settings: Settings, dataset_version: str, mode: str, root: Path = ROOT) -> dict:
    """Everything that changes the numbers of a run of this mode: selected settings, the
    code of retrieval (and generation), the corpus manifest and the dataset."""
    names = sorted(
        n
        for n in type(settings).model_fields
        if n.startswith(RETRIEVAL_SETTING_PREFIXES) or n in RETRIEVAL_SETTING_NAMES
    )
    patterns = list(RETRIEVAL_CODE)
    if mode != "retrieval":
        names += [n for n in GENERATION_SETTING_NAMES if n in type(settings).model_fields]
        patterns += GENERATION_CODE
    return {
        "mode": mode,
        "settings": {n: getattr(settings, n) for n in names},
        "code": _hash_files(patterns, root),
        "dataset_version": dataset_version,
    }


def config_hash(settings: Settings, dataset_version: str, mode: str, root: Path = ROOT) -> str:
    payload = json.dumps(
        config_payload(settings, dataset_version, mode, root), sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def git_info(root: Path = ROOT) -> dict:
    """HEAD sha and whether tracked or new files differ from it (results excluded).
    Read-only: GIT_OPTIONAL_LOCKS=0 keeps `git status` from touching the index."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout

    sha = (git("rev-parse", "HEAD") or "").strip()
    status = git(
        "status", "--porcelain", "--", ".", ":(exclude)evals/results", ":(exclude)evals/calibration"
    )
    return {"sha": sha, "dirty": bool(status and status.strip()) if status is not None else None}


def redacted_settings(settings: Settings) -> dict:
    """settings.model_dump() without secrets, for run.json."""
    data = settings.model_dump(mode="json")
    for name, value in data.items():
        secret = name.endswith("api_key") or name == "eval_key"
        secret = secret or "secret" in name or "password" in name
        if secret:
            data[name] = "***" if value else ""
    if data.get("database_url"):
        data["database_url"] = _database_location(data["database_url"])
    return data


def _database_location(url: str) -> str:
    """Only where the database is (host, port, dbname, user), from any form libpq accepts
    (URL, keyword DSN, password in the query); "***" when it cannot be parsed."""
    try:
        params = conninfo_to_dict(url)
    except psycopg.Error:
        return "***"
    kept = {k: params[k] for k in ("host", "port", "dbname", "user") if params.get(k)}
    return make_conninfo(**kept)


def _now() -> datetime:
    return datetime.now().astimezone()


# --- writing a run ----------------------------------------------------------------------------


def _new_run_dir(results_dir: Path, now: datetime, sha: str, cfg_hash: str) -> Path:
    base = f"{now:%Y%m%d-%H%M%S}_{(sha or 'nogit')[:7]}_{cfg_hash[:8]}"
    path = results_dir / base
    n = 1
    while path.exists():
        n += 1
        path = results_dir / f"{base}-{n}"
    return path


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def bump_test_counter(results_dir: Path, run_id: str, mode: str, when: datetime) -> int:
    path = results_dir / TEST_COUNTER_NAME
    data = {"count": 0, "runs": []}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    data["count"] = int(data.get("count", 0)) + 1
    data.setdefault("runs", []).append(
        {"run_id": run_id, "mode": mode, "date": when.isoformat(timespec="seconds")}
    )
    _write_json(path, data)
    return data["count"]


# --- inputs --------------------------------------------------------------------------------


@dataclass
class RunInputs:
    settings: Settings
    split: str
    dataset: LoadedDataset
    cases: list[Case]  # reviewed cases of the split, in dataset order
    clusters: dict[str, str]
    works: dict[str, Work]
    units: list[Unit] | None
    corpus_version: str
    params: MatchParams
    limit: int | None = None  # a partial run: never written as retrieval_dev_latest.json
    ids_file: str | None = None  # a partial run too: only the ids listed in this file
    include_unreviewed: bool = False  # unreviewed cases included: never published
    notes: list[str] = field(default_factory=list)
    # Reviewed unanswerable cases checked against another corpus_version: not reviewed now.
    review_expired: list[str] = field(default_factory=list)

    @property
    def writes_latest(self) -> bool:
        """Only a complete run of reviewed cases may become retrieval_dev_latest.json."""
        return self.limit is None and self.ids_file is None and not self.include_unreviewed

    @property
    def n_unreviewed(self) -> int:
        """Cases in this run that do not count as reviewed (only with include_unreviewed)."""
        expired = set(self.review_expired)
        return sum(not c.reviewed or c.id in expired for c in self.cases)


def review_expired(case: Case, corpus_version: str) -> bool:
    """A reviewed case that expects an abstention but was checked against another corpus:
    with more text it may have an answer now (PLAN: "Cada vez que cambia corpus_version
    se vuelven a revisar"). It counts as unreviewed until the person checks it again."""
    return (
        case.reviewed
        and case.expected_behavior == ExpectedBehavior.abstain
        and case.corpus_version_checked != corpus_version
    )


UNREVIEWED_NOTE = (
    "INCLUYE CASOS SIN REVISAR (--include-unreviewed): solo prueba el código; estas cifras "
    "no se publican ni se comparan"
)


def read_ids_file(path: Path) -> list[str]:
    """Case ids, one per line; blank lines and lines starting with '#' are skipped."""
    if not path.exists():
        raise RunError(f"No existe el archivo de ids {path}.")
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _select_ids(cases: list[Case], ids: list[str], dataset: LoadedDataset, split: str) -> list:
    """The cases named in `ids`, in that order. Unknown ids and ids of the other split are
    an error; ids of cases left out of `cases` (unreviewed) are skipped."""
    known = {c.id: c for c in dataset.cases}
    unknown = [i for i in ids if i not in known]
    other = [i for i in ids if i in known and known[i].split != split]
    if unknown or other:
        problems = []
        if unknown:
            problems.append(f"no existen: {', '.join(unknown)}")
        if other:
            problems.append(f"no son de {split}: {', '.join(other)}")
        raise RunError("Ids inválidos en el archivo de ids: " + "; ".join(problems))
    by_id = {c.id: c for c in cases}
    return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]


def prepare_inputs(
    *,
    split: str,
    confirm_test: bool,
    dataset_dir: Path,
    settings: Settings,
    works: dict[str, Work],
    units: list[Unit] | None,
    corpus_version: str,
    limit: int | None = None,
    ids: list[str] | None = None,
    ids_file: str | None = None,
    include_unreviewed: bool = False,
    log: Callable[[str], None] = print,
) -> RunInputs:
    if split == "test" and not confirm_test:
        raise RunError(
            "`test` se reserva para los hitos (línea base, sistema final, cierre de nivel). "
            "Para correrlo: --confirm-test (make eval SPLIT=test CONFIRM_TEST=1)."
        )
    if split == "test" and include_unreviewed:
        raise RunError("--include-unreviewed solo se permite sobre dev.")
    dataset = load_dataset(dataset_dir)
    errors = validate_labels(dataset, works=works, units=units)
    if errors:
        shown = "\n".join(f"  - {e}" for e in errors[:50])
        more = f"\n  ... y {len(errors) - 50} más" if len(errors) > 50 else ""
        raise RunError(f"Etiquetas inválidas ({len(errors)} problemas):\n{shown}{more}")
    expired = [c.id for c in dataset.reviewed if review_expired(c, corpus_version)]
    reviewed = [c for c in dataset.reviewed if c.id not in expired]
    ignored = len(dataset.unreviewed)
    pool = dataset.cases if include_unreviewed else reviewed
    cases = [c for c in pool if c.split == split]
    if ids is not None:
        cases = _select_ids(cases, ids, dataset, split)
    if limit is not None:
        cases = cases[:limit]
    expired_note = (
        f"revisión vencida: {len(expired)} casos sin respuesta se revisaron contra otro "
        f"corpus_version (el actual es {corpus_version}); hay que volver a revisarlos"
    )
    if include_unreviewed:
        n_unreviewed = sum(not c.reviewed or c.id in expired for c in cases)
        log(
            f"Dataset {dataset.version}: {len(reviewed)} casos revisados. ATENCIÓN: se incluyen "
            f"los casos SIN REVISAR (--include-unreviewed). En {split}: {len(cases)} casos, "
            f"{n_unreviewed} sin revisar "
            f"({sum(c.scored_for_retrieval for c in cases)} con evidencia para retrieval)."
        )
    else:
        log(
            f"Dataset {dataset.version}: {len(reviewed)} casos revisados; ignorados por no tener "
            f"revisado_por: {ignored}"
            + (f"; ignorados por {expired_note}" if expired else "")
            + f". En {split}: {len(cases)} casos "
            f"({sum(c.scored_for_retrieval for c in cases)} con evidencia para retrieval)."
        )
    if not cases:
        raise RunError(f"No hay casos revisados en {split}: nada que evaluar.")
    notes = []
    if include_unreviewed:
        notes.append(UNREVIEWED_NOTE)
    if expired:
        notes.append(expired_note)
    if ids_file is not None or ids is not None:
        notes.append(f"corrida parcial: solo los {len(cases)} casos de {ids_file or 'una lista'}")
    if limit is not None:
        notes.append(f"corrida parcial: solo los primeros {limit} casos")
    return RunInputs(
        settings=settings,
        split=split,
        dataset=dataset,
        cases=cases,
        clusters=evidence_clusters(dataset.cases),
        works=works,
        units=units,
        corpus_version=corpus_version,
        params=MatchParams.from_settings(settings),
        limit=limit,
        ids_file=ids_file if ids is not None else None,
        include_unreviewed=include_unreviewed,
        notes=notes,
        review_expired=expired,
    )


def _case_fields(case: Case, inputs: RunInputs) -> dict:
    return {
        "id": case.id,
        "type": case.type.value,
        "question_lang": case.question_lang,
        "expected_behavior": case.expected_behavior.value,
        "split": case.split.value,
        "cluster": inputs.clusters.get(case.id, case.id),
        "evidence": [[span.label() for span in group] for group in case.evidence],
        "works": sorted({span.work_id for group in case.evidence for span in group}),
        "scored": case.scored_for_retrieval,
    }


def _hits_json(hits: Sequence[Any], hit_groups: list[list[int]]) -> list[dict]:
    return [
        {
            "rank": h.rank,
            "score": round(float(h.score), 6),
            "method": h.method,
            **Location.from_chunk(h.chunk).to_json(),
            "groups": groups,
        }
        for h, groups in zip(hits, hit_groups, strict=True)
    ]


def similarity_gate(hits: Sequence[Any], k: int) -> dict:
    """What app.generation's similarity gate sees in the first k hits, for calibrating
    ABSTAIN_MIN_SIMILARITY: the best dense score ("score"); "always_pass" when the gate
    never applies (an explicit Bekker reference, or no dense hit); "always_abstain" when
    retrieval found nothing."""
    from app.generation import similarity_gate as gate_of

    gate, score = gate_of(list(hits)[:k])
    return {"gate": gate, "gate_score": round(float(score), 6) if score is not None else None}


# --- retrieval mode --------------------------------------------------------------------------


def retrieval_record(case: Case, hits: Sequence[Any], inputs: RunInputs) -> dict:
    s = inputs.settings
    locations = [Location.from_chunk(h.chunk) for h in hits]
    hit_groups = groups_hit_by(locations, case.evidence, inputs.params)
    first = ranks_from_hits(hit_groups, len(case.evidence))
    scored = case.scored_for_retrieval
    return {
        **_case_fields(case, inputs),
        "first_hit_ranks": first,
        "recall_at_k": recall_at(first, s.retrieval_k) if scored else None,
        "recall_at_candidates": recall_at(first, s.retrieval_candidates) if scored else None,
        "all_evidence_at_k": all_evidence_at(first, s.retrieval_k) if scored else None,
        "all_evidence_at_candidates": (
            all_evidence_at(first, s.retrieval_candidates) if scored else None
        ),
        "rr_at_10": reciprocal_rank(first, MRR_CUTOFF) if scored else None,
        "top_method": hits[0].method if hits else None,
        **similarity_gate(hits, s.retrieval_k),
        "hits": _hits_json(hits, hit_groups),
    }


def _est(values: Sequence[Any], s: Settings) -> dict:
    return stats.estimate(values, resamples=s.bootstrap_resamples, seed=s.bootstrap_seed).to_json()


def _breakdown(records: list[dict], key: str, metrics: Sequence[str], s: Settings) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in records:
        value = r.get(key)
        label = "+".join(value) if isinstance(value, list) else str(value)
        groups.setdefault(label, []).append(r)
    return {
        label: {m: _est([r[m] for r in rows], s) for m in metrics}
        for label, rows in sorted(groups.items())
    }


def summarize_retrieval(records: list[dict], s: Settings) -> dict:
    scored = [r for r in records if r["scored"]]
    keys = (
        "recall_at_k",
        "recall_at_candidates",
        "all_evidence_at_k",
        "all_evidence_at_candidates",
        "rr_at_10",
    )
    by_metrics = ("recall_at_k", "recall_at_candidates")
    return {
        "k": s.retrieval_k,
        "candidates": s.retrieval_candidates,
        "n_cases": len(records),
        "n_scored": len(scored),
        "metrics": {key: _est([r[key] for r in scored], s) for key in keys},
        "by": {
            "type": _breakdown(scored, "type", by_metrics, s),
            "question_lang": _breakdown(scored, "question_lang", by_metrics, s),
            "work": _breakdown(scored, "works", by_metrics, s),
        },
    }


# --- full mode -------------------------------------------------------------------------------


def _passage_locator(chunk: Any) -> str:
    divisions = ".".join(label for _, label in chunk.divisions)
    location = bekker.format_range(chunk.bekker_start, chunk.bekker_end) or divisions
    return f"{chunk.work_id} ({chunk.edition_id}) {location}".strip()


def _rule_verdict(criterion: str, reason: str) -> Verdict:
    return Verdict(criterion=criterion, verdict="fail", reason=reason, source="rule")


def judge_answer(
    judge: Any,
    case: Case,
    *,
    answer: str,
    answered: bool,
    passages: Sequence[tuple[int, str, str]],
) -> dict[str, Verdict]:
    """One judge call per applicable criterion. Without an answer, key points and premise
    correction fail by rule (no call)."""
    verdicts: dict[str, Verdict] = {}
    for criterion in criteria_for(case, answered=answered):
        if not answered:
            verdicts[criterion] = _rule_verdict(criterion, "sin respuesta: se abstuvo o falló")
            continue
        verdicts[criterion] = judge.evaluate(
            criterion,
            question=case.question,
            answer=answer,
            context=passages,
            key_points=case.key_points,
        )
    return verdicts


def full_record(
    case: Case,
    result: Any,
    inputs: RunInputs,
    *,
    verdicts: dict[str, Verdict] | None,
    wall_ms: float,
    llm_cached: bool,
) -> dict:
    s = inputs.settings
    context = [Location.from_chunk(r.chunk) for r in result.retrieved]
    hit_groups = groups_hit_by(context, case.evidence, inputs.params)
    first = ranks_from_hits(hit_groups, len(case.evidence))
    check = check_citations(result.citations, context, case.evidence, inputs.params)
    cites_dubious = any(
        str(getattr(c.authenticity, "value", c.authenticity)) != "genuine" for c in result.citations
    )
    failed = bool(result.search_only or result.error)
    answered = not result.abstained and not failed
    simple = {c: v.verdict for c, v in verdicts.items()} if verdicts is not None else None
    if simple is None:
        strict = None  # no judge: undefined for every case (see metrics.strict_pass)
    elif failed:
        strict = False
    else:
        strict = strict_pass(
            case,
            abstained=result.abstained,
            cites_dubious=cites_dubious,
            citations=check,
            verdicts=simple,
        )
    scored = case.scored_for_retrieval
    return {
        **_case_fields(case, inputs),
        "answer": result.answer,
        "answer_lang": result.answer_lang,
        "abstained": result.abstained,
        "abstain_reason": result.abstain_reason,
        "search_only": result.search_only,
        "error": result.error,
        "failed": failed,
        "answered": answered,
        "behavior_ok": (
            False
            if failed
            else behavior_met(case, abstained=result.abstained, cites_dubious=cites_dubious)
        ),
        "first_hit_ranks": first,
        "recall_at_k": recall_at(first, s.retrieval_k) if scored else None,
        "rr_at_10": reciprocal_rank(first, MRR_CUTOFF) if scored else None,
        **similarity_gate(result.retrieved, s.retrieval_k),
        "context": _hits_json(result.retrieved, hit_groups),
        "citations": [
            {
                "n": c.n,
                "chunk_id": c.chunk_id,
                "work_id": c.work_id,
                "edition_id": c.edition_id,
                "bekker": bekker.format_range(c.bekker_start, c.bekker_end),
                "authenticity": str(getattr(c.authenticity, "value", c.authenticity)),
                **detail,
            }
            for c, detail in zip(result.citations, check.details, strict=True)
        ],
        "citation_check": check.to_json(),
        "has_citation": check.n_citations > 0 if answered else None,
        "citations_valid": check.all_valid if answered else None,
        "citations_in_context": check.all_in_context if answered else None,
        "citation_precision": check.precision if answered else None,
        "citation_recall": check.recall if answered else None,
        "cites_dubious": cites_dubious,
        "lang_ok": detect_lang(result.answer) == case.question_lang if answered else None,
        "key_points_lexical": key_points_lexical(result.answer, case.key_points)
        if answered
        else None,
        "judge": {c: v.to_json() for c, v in verdicts.items()} if verdicts is not None else None,
        "strict_pass": strict,
        "model": result.model,
        "provider": result.provider,
        "prompt_version": result.prompt_version,
        "index_version": result.index_version,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "latency_ms": result.latency_ms,
        "wall_ms": round(wall_ms, 1),
        "llm_cached": llm_cached,
    }


def _judge_values(records: list[dict], criterion: str) -> list[int]:
    out = []
    for r in records:
        verdict = (r.get("judge") or {}).get(criterion)
        if verdict is not None:
            out.append(1 if verdict["verdict"] == "pass" else 0)
    return out


def _judge_counts(records: list[dict]) -> dict:
    counts: dict[str, Counter] = {}
    for r in records:
        for criterion, v in (r.get("judge") or {}).items():
            c = counts.setdefault(criterion, Counter())
            c[v["verdict"]] += 1
            c["parse_errors"] += int(not v.get("parse_ok", True))
            c["by_rule"] += int(v.get("source") == "rule")
    return {k: dict(v) for k, v in sorted(counts.items())}


def _usage(records: list[dict], s: Settings) -> dict:
    uncached = [r["wall_ms"] for r in records if not r.get("llm_cached")]
    n = len(records)
    mean_in = sum(r.get("tokens_in") or 0 for r in records) / n if n else 0.0
    mean_out = sum(r.get("tokens_out") or 0 for r in records) / n if n else 0.0
    usd = (mean_in * s.eval_llm_usd_per_mtok_in + mean_out * s.eval_llm_usd_per_mtok_out) / 1e6
    return {
        "latency_p50_ms": stats.percentile(uncached, 50),
        "latency_p95_ms": stats.percentile(uncached, 95),
        "latency_n": len(uncached),
        "tokens_in_mean": round(mean_in, 1),
        "tokens_out_mean": round(mean_out, 1),
        "usd_per_1000_queries": round(usd * 1000, 4),
    }


def _abstention(records: list[dict], s: Settings) -> dict:
    ok = [r for r in records if not r["failed"]]
    abstain_cases = [r for r in ok if r["expected_behavior"] == "abstain"]
    answer_cases = [r for r in ok if r["expected_behavior"] != "abstain"]
    return {
        "false_answer_rate": _est([not r["abstained"] for r in abstain_cases], s),
        "false_refusal_rate": _est([r["abstained"] for r in answer_cases], s),
    }


def summarize_full(records: list[dict], s: Settings) -> dict:
    scored = [r for r in records if r["scored"]]
    answered = [r for r in records if r["answered"]]
    judged = any(r.get("judge") is not None for r in records)
    metrics = {
        "recall_at_k": _est([r["recall_at_k"] for r in scored], s),
        "strict_pass": _est([r["strict_pass"] for r in records], s),
        "faithfulness": _est(_judge_values(records, "faithfulness"), s),
        **_abstention(records, s),
        "behavior_ok": _est([r["behavior_ok"] for r in records], s),
        "key_points": _est(_judge_values(records, "key_points"), s),
        "premise_corrected": _est(_judge_values(records, "premise_corrected"), s),
        "mrr_at_10": _est([r["rr_at_10"] for r in scored], s),
        "has_citation": _est([r["has_citation"] for r in answered], s),
        "citations_valid": _est([r["citations_valid"] for r in answered], s),
        "citations_in_context": _est([r["citations_in_context"] for r in answered], s),
        "citation_precision": _est([r["citation_precision"] for r in answered], s),
        "citation_recall": _est([r["citation_recall"] for r in answered], s),
        "lang_ok": _est([r["lang_ok"] for r in answered], s),
        "key_points_lexical": _est([r["key_points_lexical"] for r in answered], s),
    }
    return {
        "k": s.retrieval_k,
        "n_cases": len(records),
        "n_answered": len(answered),
        "n_abstained": sum(r["abstained"] for r in records),
        "n_failed": sum(r["failed"] for r in records),
        "judged": judged,
        "judge_counts": _judge_counts(records),
        "metrics": metrics,
        "usage": _usage(records, s),
        "by": {
            "type": _breakdown(records, "type", ("strict_pass", "behavior_ok"), s),
            "question_lang": _breakdown(records, "question_lang", ("strict_pass",), s),
        },
    }


# --- baseline without retrieval ---------------------------------------------------------------


def _unit_slice(unit: Unit, start: int, end: int) -> str:
    """Text of `unit` between the Bekker anchors around [start, end] (whole unit if none)."""
    begin, stop = 0, len(unit.text)
    for offset, value in sorted(unit.bekker_anchors):
        if value <= start:
            begin = offset
        elif value > end:
            stop = offset
            break
    return unit.text[begin:stop].strip()


def gold_passages(case: Case, units: Sequence[Unit], s: Settings) -> list[tuple[int, str, str]]:
    """Text of the first location of each evidence group, for judging the faithfulness of
    an answer written without retrieval. Kept in memory only; never written to results."""
    budget = int(s.context_max_tokens * s.chars_per_token / max(1, len(case.evidence)))
    resolve = MatchParams(0, 1)
    passages = []
    for n, group in enumerate(case.evidence, start=1):
        span = group[0]
        texts = []
        for unit in units:
            if unit.text_type != TextType.aristotle or not span_hit(
                unit_location(unit), span, resolve
            ):
                continue
            if isinstance(span, BekkerSpan) and unit.bekker_anchors:
                texts.append(_unit_slice(unit, *span.encoded()))
            else:
                texts.append(unit.text)
        passages.append((n, span.label(), "\n".join(texts)[:budget]))
    return passages


def parse_baseline(text: str) -> tuple[str, bool, bool]:
    """(answer, abstained, parse_ok) from the baseline prompt's JSON answer."""
    cleaned = (text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("answer"), str):
            return data["answer"], data.get("answerable") is False, True
    return cleaned, False, False


def baseline_record(
    case: Case,
    response: Any,
    inputs: RunInputs,
    *,
    verdicts: dict[str, Verdict] | None,
    wall_ms: float,
    llm_cached: bool,
) -> dict:
    answer, abstained, parse_ok = parse_baseline(getattr(response, "text", ""))
    answered = not abstained
    refs = bekker.find_refs(answer) if answered else []
    gold = [s for group in case.evidence for s in group if isinstance(s, BekkerSpan)]
    refs_ok = sum(
        any(bekker_overlap_hit(ref, span.encoded(), inputs.params) for span in gold) for ref in refs
    )
    return {
        **_case_fields(case, inputs),
        "answer": answer,
        "abstained": abstained,
        "answered": answered,
        "failed": False,
        "parse_ok": parse_ok,
        "behavior_ok": behavior_met(case, abstained=abstained, cites_dubious=False),
        "bekker_refs": [bekker.format_range(a, b) for a, b in refs],
        "mentions_bekker": bool(refs) if answered else None,
        "bekker_ref_precision": refs_ok / len(refs) if refs and gold else None,
        "lang_ok": detect_lang(answer) == case.question_lang if answered else None,
        "key_points_lexical": key_points_lexical(answer, case.key_points) if answered else None,
        "judge": {c: v.to_json() for c, v in verdicts.items()} if verdicts is not None else None,
        "model": getattr(response, "model", ""),
        "provider": getattr(response, "provider", ""),
        "tokens_in": getattr(response, "tokens_in", 0) or 0,
        "tokens_out": getattr(response, "tokens_out", 0) or 0,
        "wall_ms": round(wall_ms, 1),
        "llm_cached": llm_cached,
    }


def summarize_baseline(records: list[dict], s: Settings) -> dict:
    answered = [r for r in records if r["answered"]]
    return {
        "n_cases": len(records),
        "n_answered": len(answered),
        "n_abstained": sum(r["abstained"] for r in records),
        "n_unparsed": sum(not r["parse_ok"] for r in records),
        "judged": any(r.get("judge") is not None for r in records),
        "judge_counts": _judge_counts(records),
        "metrics": {
            **_abstention(records, s),
            "behavior_ok": _est([r["behavior_ok"] for r in records], s),
            "key_points": _est(_judge_values(records, "key_points"), s),
            "faithfulness_vs_gold": _est(_judge_values(records, "faithfulness"), s),
            "mentions_bekker": _est([r["mentions_bekker"] for r in answered], s),
            "bekker_ref_precision": _est([r["bekker_ref_precision"] for r in answered], s),
            "lang_ok": _est([r["lang_ok"] for r in answered], s),
            "key_points_lexical": _est([r["key_points_lexical"] for r in answered], s),
        },
        "usage": _usage(records, s),
    }


def finish_run(
    inputs: RunInputs,
    *,
    mode: str,
    records: list[dict],
    summary: dict,
    results_dir: Path,
    index_version: str,
    models: dict,
    extra: dict | None = None,
    now: datetime | None = None,
    root: Path = ROOT,
    log: Callable[[str], None] = print,
) -> Path:
    s = inputs.settings
    now = now or _now()
    git = git_info(root)
    cfg = config_hash(s, inputs.dataset.version, mode, root)
    run_dir = _new_run_dir(results_dir, now, git["sha"], cfg)
    warnings = list(inputs.notes)
    if (models.get("embedding") or {}).get("provider") == "fake":
        warnings.append(
            "embeddings falsos (EMBEDDING_PROVIDER=fake): estas cifras no miden el modelo real"
        )
    if any(r.get("provider") == "fake" for r in records):
        warnings.append("LLM falso (LLM_PROVIDER=fake): las respuestas son simuladas")
    judge_meta = models.get("judge") or {}
    if judge_meta and any(
        v.get("provider") == "fake" for r in records for v in (r.get("judge") or {}).values()
    ):
        warnings.append("juez falso (LLM_PROVIDER=fake): los veredictos son simulados")
    meta = {
        "run_id": run_dir.name,
        "mode": mode,
        "split": inputs.split,
        "created_at": now.isoformat(timespec="seconds"),
        "git": git,
        "config_hash": cfg,
        "corpus_version": inputs.corpus_version,
        "index_version": index_version,
        "dataset": {
            "version": inputs.dataset.version,
            "files": [p.name for p in inputs.dataset.files],
            "n_cases_total": len(inputs.dataset.cases),
            "n_reviewed": len(inputs.dataset.reviewed) - len(inputs.review_expired),
            "n_ignored_unreviewed": (
                0 if inputs.include_unreviewed else len(inputs.dataset.unreviewed)
            ),
            "n_split": len(inputs.cases),
            "includes_unreviewed": inputs.include_unreviewed,
            "n_unreviewed_included": inputs.n_unreviewed,
            "n_review_expired": len(inputs.review_expired),
        },
        "corpus_units": len(inputs.units) if inputs.units is not None else None,
        "limit": inputs.limit,
        "ids_file": inputs.ids_file,
        "models": models,
        "thresholds": {
            "retrieval_k": s.retrieval_k,
            "retrieval_candidates": s.retrieval_candidates,
            "eval_overlap_min_lines": s.eval_overlap_min_lines,
            "eval_overlap_tolerance_lines": s.eval_overlap_tolerance_lines,
            "abstain_min_similarity": s.abstain_min_similarity,
            "context_max_tokens": s.context_max_tokens,
            "output_max_tokens": s.output_max_tokens,
        },
        "bootstrap": {"seed": s.bootstrap_seed, "resamples": s.bootstrap_resamples},
        "settings": redacted_settings(s),
        "warnings": warnings,
        "summary": summary,
        **(extra or {}),
    }
    run_dir.mkdir(parents=True)
    _write_json(run_dir / "run.json", meta)
    with (run_dir / "per_question.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary_md = render_summary(meta, summary)
    (run_dir / "summary.md").write_text(summary_md, encoding="utf-8")
    if mode == "retrieval" and inputs.split == "dev" and inputs.writes_latest:
        _write_json(
            results_dir / LATEST_NAME,
            {
                "config_hash": cfg,
                "dataset_version": inputs.dataset.version,
                "corpus_version": inputs.corpus_version,
                "index_version": index_version,
                "embedding": models.get("embedding"),
                "split": inputs.split,
                "k": s.retrieval_k,
                "candidates": s.retrieval_candidates,
                "n_scored": summary["n_scored"],
                "metrics": summary["metrics"],
            },
        )
    if inputs.split == "test":
        count = bump_test_counter(results_dir, run_dir.name, mode, now)
        log(f"Corrida número {count} sobre test (registrada en {TEST_COUNTER_NAME}).")
    log(summary_md)
    log(f"Resultados en {run_dir}")
    return run_dir


# --- entry points (dependencies injected) -------------------------------------------------------


def run_retrieval_eval(
    inputs: RunInputs,
    *,
    retriever: Retriever,
    results_dir: Path,
    index_version: str,
    embedding: dict,
    now: datetime | None = None,
    root: Path = ROOT,
    log: Callable[[str], None] = print,
) -> Path:
    records = [retrieval_record(case, retriever(case.question), inputs) for case in inputs.cases]
    summary = summarize_retrieval(records, inputs.settings)
    return finish_run(
        inputs,
        mode="retrieval",
        records=records,
        summary=summary,
        results_dir=results_dir,
        index_version=index_version,
        models={"embedding": embedding},
        now=now,
        root=root,
        log=log,
    )


def _llm_meta(records: list[dict], configured: str) -> dict:
    answered_by = Counter((r.get("model") or "", r.get("provider") or "") for r in records)
    return {
        "model": configured,
        "answered_by": [
            {"model": m, "provider": p, "n": n} for (m, p), n in sorted(answered_by.items())
        ],
    }


def _judge_meta(judge: Any | None) -> dict | None:
    if judge is None:
        return None
    return {
        "model": judge.model_id,
        "prompts": judge.prompt_versions,
        "prompt_sha256": {c: p.sha256 for c, p in judge.prompts.items()},
    }


def _check_quota(*llms: Any) -> None:
    for llm in llms:
        if llm is not None and getattr(llm, "exhausted", False):
            raise QuotaExhausted(
                "Se agotó el cupo del proveedor (429 tras todos los reintentos). Volver a "
                "correr el mismo comando reanuda desde la caché (evals/.cache/)."
            )


def _cache_state(*llms: Any) -> tuple[int, int]:
    hits = sum(getattr(llm, "hits", 0) for llm in llms if llm is not None)
    misses = sum(getattr(llm, "misses", 0) for llm in llms if llm is not None)
    return hits, misses


def run_full_eval(
    inputs: RunInputs,
    *,
    answerer: Answerer,
    generator_llm: Any,
    judge: Any | None,
    results_dir: Path,
    index_version: str,
    embedding: dict,
    now: datetime | None = None,
    root: Path = ROOT,
    log: Callable[[str], None] = print,
) -> Path:
    s = inputs.settings
    judge_llm = getattr(judge, "llm", None)
    records = []
    for i, case in enumerate(inputs.cases, start=1):
        before = _cache_state(generator_llm)
        t0 = time.perf_counter()
        result = answerer(case.question)
        wall_ms = (time.perf_counter() - t0) * 1000
        _check_quota(generator_llm)
        after = _cache_state(generator_llm)
        cached = after[1] == before[1] and after[0] > before[0]
        failed = bool(result.search_only or result.error)
        answered = not result.abstained and not failed
        verdicts = None
        if judge is not None:
            passages = [
                (n, _passage_locator(r.chunk), r.chunk.text)
                for n, r in enumerate(result.retrieved, start=1)
            ]
            verdicts = judge_answer(
                judge, case, answer=result.answer, answered=answered, passages=passages
            )
            _check_quota(judge_llm)
        records.append(
            full_record(case, result, inputs, verdicts=verdicts, wall_ms=wall_ms, llm_cached=cached)
        )
        if i == FAIL_FAST_CASES and all(r["failed"] for r in records):
            raise RunError(
                f"Las primeras {i} preguntas fallaron en el LLM ({result.error}). Revisar LLM_* "
                "(clave, modelo, URL) antes de seguir; no se guardó nada."
            )
        log(
            f"  [{i}/{len(inputs.cases)}] {case.id}: "
            + ("error del LLM" if failed else "abstención" if result.abstained else "respuesta")
        )
    summary = summarize_full(records, s)
    prompt_versions = sorted({r["prompt_version"] for r in records if r["prompt_version"]})
    hits, misses = _cache_state(generator_llm, judge_llm)
    models = {
        "embedding": embedding,
        "generator": {
            **_llm_meta(records, s.llm_model),
            "reasoning_effort": s.llm_reasoning_effort,
        },
        "judge": _judge_meta(judge),
    }
    extra = {"prompt_versions": prompt_versions, "cache": {"hits": hits, "misses": misses}}
    return finish_run(
        inputs,
        mode="full",
        records=records,
        summary=summary,
        results_dir=results_dir,
        index_version=index_version,
        models=models,
        extra=extra,
        now=now,
        root=root,
        log=log,
    )


def run_baseline_eval(
    inputs: RunInputs,
    *,
    llm: Any,
    prompt: Prompt,
    judge: Any | None,
    results_dir: Path,
    now: datetime | None = None,
    root: Path = ROOT,
    log: Callable[[str], None] = print,
) -> Path:
    s = inputs.settings
    if judge is not None and inputs.units is None:
        raise RunError("La línea base con juez necesita el corpus procesado (make corpus).")
    judge_llm = getattr(judge, "llm", None)
    records = []
    for i, case in enumerate(inputs.cases, start=1):
        before = _cache_state(llm)
        t0 = time.perf_counter()
        response = llm.complete(
            prompt.render(question=case.question), max_tokens=s.output_max_tokens, temperature=0
        )
        wall_ms = (time.perf_counter() - t0) * 1000
        after = _cache_state(llm)
        answer, abstained, _ = parse_baseline(getattr(response, "text", ""))
        verdicts = None
        if judge is not None:
            gold = gold_passages(case, inputs.units or [], s)
            verdicts = judge_answer(
                judge, case, answer=answer, answered=not abstained, passages=gold
            )
            _check_quota(judge_llm)
        records.append(
            baseline_record(
                case,
                response,
                inputs,
                verdicts=verdicts,
                wall_ms=wall_ms,
                llm_cached=after[1] == before[1] and after[0] > before[0],
            )
        )
        log(f"  [{i}/{len(inputs.cases)}] {case.id}")
    summary = summarize_baseline(records, s)
    hits, misses = _cache_state(llm, judge_llm)
    models = {
        "embedding": None,
        "generator": {**_llm_meta(records, s.llm_model), "prompt": prompt.version},
        "judge": _judge_meta(judge),
    }
    extra = {"prompt_versions": [prompt.version], "cache": {"hits": hits, "misses": misses}}
    return finish_run(
        inputs,
        mode="baseline",
        records=records,
        summary=summary,
        results_dir=results_dir,
        index_version="",
        models=models,
        extra=extra,
        now=now,
        root=root,
        log=log,
    )


# --- CI check of retrieval_dev_latest.json -------------------------------------------------------


def check_latest(
    settings: Settings,
    *,
    dataset_dir: Path = DATASET_DIR,
    results_dir: Path = RESULTS_DIR,
    baseline_file: Path | None = None,
    root: Path = ROOT,
) -> tuple[list[str], list[str]]:
    """(problems, notes). Problems: the summary is missing, does not match the commit's
    configuration, was made with fake embeddings, or its recall@k is below main's."""
    path = results_dir / LATEST_NAME
    dataset = load_dataset(dataset_dir)
    if not any(c.split == "dev" for c in dataset.reviewed):
        return [], ["sin casos revisados en dev: no hay resumen de retrieval que comprobar"]
    if not path.exists():
        return [f"falta {path.name}: correr `make eval-fast` y subir el resumen"], []
    latest = json.loads(path.read_text(encoding="utf-8"))
    expected = config_hash(settings, dataset.version, "retrieval", root)
    problems, notes = [], []
    if latest.get("config_hash") != expected:
        problems.append(
            f"{path.name} no corresponde a la configuración del commit (hash "
            f"{latest.get('config_hash')}, esperado {expected}): correr `make eval-fast` y "
            "subir el resumen"
        )
    model = (latest.get("embedding") or {}).get("model")
    if model != settings.embedding_model:
        problems.append(
            f"{path.name} se hizo con embeddings {model!r} y la configuración usa "
            f"{settings.embedding_model!r}"
        )
    if baseline_file is not None and baseline_file.exists():
        base = json.loads(baseline_file.read_text(encoding="utf-8"))
        if base.get("dataset_version") != latest.get("dataset_version"):
            notes.append("el dataset cambió respecto de main: el recall no se compara")
        else:
            new = latest["metrics"]["recall_at_k"]["value"] or 0.0
            old = base["metrics"]["recall_at_k"]["value"] or 0.0
            if new < old - 1e-9:
                problems.append(
                    f"el recall de evidencia en k bajó respecto de main: {pct(old)} -> {pct(new)}"
                )
            else:
                notes.append(f"recall de evidencia en k: main {pct(old)}, ahora {pct(new)}")
    return problems, notes


# --- CLI ---------------------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Runner de evals (retrieval, completo, línea base).")
    p.add_argument("--mode", choices=MODES, default="retrieval")
    p.add_argument("--split", choices=("dev", "test"), default="dev")
    p.add_argument("--confirm-test", action="store_true", help="necesario para correr test")
    p.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    p.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    p.add_argument("--limit", type=int, default=None, help="solo los primeros N casos")
    p.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="solo los casos cuyos ids lista este archivo (p. ej. evals/pr_subset.txt)",
    )
    p.add_argument(
        "--include-unreviewed",
        action="store_true",
        help="incluir los casos sin revisar (solo dev; prueba el código, no se publica)",
    )
    p.add_argument("--no-judge", action="store_true", help="sin juez: solo métricas por reglas")
    p.add_argument("--cache-dir", type=Path, default=CACHE_DIR, help="caché de respuestas del LLM")
    p.add_argument(
        "--check-latest",
        action="store_true",
        help=f"comprobar {LATEST_NAME} contra la configuración (CI)",
    )
    p.add_argument(
        "--baseline-file",
        type=Path,
        default=None,
        help=f"{LATEST_NAME} de main, para comprobar que el recall no bajó",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    s = get_settings()
    if args.check_latest:
        problems, notes = check_latest(
            s,
            dataset_dir=args.dataset_dir,
            results_dir=args.results_dir,
            baseline_file=args.baseline_file,
        )
        for note in notes:
            print(f"  {note}")
        for problem in problems:
            print(f"  ERROR {problem}")
        print("Resumen de retrieval al día." if not problems else "Resumen de retrieval inválido.")
        return 1 if problems else 0

    from app.corpus.catalog import corpus_version, load_units, load_works
    from app.llm import LLMError

    try:
        if args.split == "test" and not args.confirm_test:
            raise RunError("`test` necesita --confirm-test (make eval SPLIT=test CONFIRM_TEST=1).")
        try:
            works, units, cv = load_works(), load_units(), corpus_version()
        except FileNotFoundError as exc:
            raise RunError(f"Falta el corpus procesado: {exc}") from exc
        ids_file = args.ids_file
        inputs = prepare_inputs(
            split=args.split,
            confirm_test=args.confirm_test,
            dataset_dir=args.dataset_dir,
            settings=s,
            works=works,
            units=units,
            corpus_version=cv,
            limit=args.limit,
            ids=read_ids_file(ids_file) if ids_file is not None else None,
            ids_file=ids_file.as_posix() if ids_file is not None else None,
            include_unreviewed=args.include_unreviewed,
        )
        if args.mode == "retrieval":
            return _main_retrieval(inputs, args)
        return _main_with_llm(inputs, args)
    except RunError as exc:
        print(f"ERROR {exc}")
        return 1
    except CostLimitExceeded as exc:
        print(f"ABORTADO {exc}")
        return 3
    except QuotaExhausted as exc:
        print(f"CORTADO {exc}")
        return 2
    except JudgeNotConfigured as exc:
        print(f"ERROR {exc}")
        return 1
    except psycopg.OperationalError as exc:
        print(f"ERROR No se pudo conectar con la base ({exc}). ¿`make db` y `make migrate`?")
        return 1
    except LLMError as exc:
        print(f"ERROR El LLM falló ({exc}). Volver a correr reanuda desde la caché.")
        return 1


def _main_retrieval(inputs: RunInputs, args: argparse.Namespace) -> int:
    from app.db import connect
    from app.embeddings import get_embedder
    from app.retrieval import IndexNotReadyError, current_index_version, retrieve

    s = inputs.settings
    embedder = get_embedder(s)
    embedding = {"model": embedder.name, "provider": embedder.provider, "dim": embedder.dim}
    try:
        with connect(s) as conn:
            index_version = current_index_version(conn)

            def retriever(question: str) -> list[Any]:
                return retrieve(
                    question, candidates=s.retrieval_candidates, conn=conn, embedder=embedder
                )

            run_retrieval_eval(
                inputs,
                retriever=retriever,
                results_dir=args.results_dir,
                index_version=index_version,
                embedding=embedding,
            )
    except IndexNotReadyError as exc:
        raise RunError(str(exc)) from exc
    return 0


def _main_with_llm(inputs: RunInputs, args: argparse.Namespace) -> int:
    from app.db import connect
    from app.embeddings import get_embedder
    from app.llm import config_problems, get_llm
    from app.retrieval import current_index_version
    from evals.judge import get_judge, judge_settings

    s = inputs.settings
    problems = config_problems(s)
    if not args.no_judge and s.judge_model:
        problems += [f"juez: {p}" for p in config_problems(judge_settings(s))]
    if problems:
        raise RunError("Configuración del LLM incompleta: " + "; ".join(problems))
    cache = ResponseCache(args.cache_dir)
    judge = None if args.no_judge else get_judge(s, cache)
    estimate = estimate_cost(
        inputs.cases,
        s,
        judge_calls_per_case=max_judge_calls if judge else None,
        with_context=args.mode == "full",
    )
    print(estimate.describe())
    guard(estimate, s)
    llm = CachingLLM.from_settings(get_llm(s), model_id=s.llm_model, settings=s, cache=cache)
    if args.mode == "baseline":
        prompt = load_prompt(PROMPTS_DIR / BASELINE_PROMPT)
        run_baseline_eval(inputs, llm=llm, prompt=prompt, judge=judge, results_dir=args.results_dir)
        return 0

    embedder = get_embedder(s)
    embedding = {"model": embedder.name, "provider": embedder.provider, "dim": embedder.dim}
    from app.generation import answer  # Etapa 4; imported only for full runs

    with connect(s) as conn:
        index_version = current_index_version(conn)

        def answerer(question: str) -> Any:
            return answer(question, llm=llm, conn=conn)

        run_full_eval(
            inputs,
            answerer=answerer,
            generator_llm=llm,
            judge=judge,
            results_dir=args.results_dir,
            index_version=index_version,
            embedding=embedding,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
