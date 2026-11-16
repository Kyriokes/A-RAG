"""`make smoke`: 10 fixed questions through the whole system (PLAN, Etapa 4).

    python -m evals.smoke                       # in process, app.generation.answer
    python -m evals.smoke --api https://...     # against a running backend's POST /ask

Questions come from evals/smoke.jsonl (format: evals/dataset/README.md). Checks, per
question: the response validates against its schema (AskResult in process, the API's
AskResponse over HTTP); the LLM answered (no search-only mode or error); every
non-abstained answer has at least one citation; every citation points to a retrieved
fragment; questions without an answer end in abstention. Exit code 0 only when every
check passes. A question that cannot be answered at all (no database, no index, the API
down or refusing) fails with the reason, and the run goes on.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.models import AskResult
from app.settings import Settings, get_settings
from evals.schema import SMOKE_FILE, SmokeCase, load_smoke

EXPECTED_CASES = 10


def _retrieved_ids(result: Any) -> set[str]:
    """Chunk ids of the retrieved fragments: AskResult carries the chunks, the API's
    AskResponse only references them."""
    return {r.chunk.chunk_id if hasattr(r, "chunk") else r.chunk_id for r in result.retrieved}


def check_result(case: SmokeCase, result: Any) -> list[str]:
    """Problems with one answer (empty when it passes). `result`: AskResult or AskResponse."""
    problems = []
    if result.search_only or result.error:
        problems.append(f"el LLM no respondió (solo búsqueda; error: {result.error})")
    retrieved = _retrieved_ids(result)
    if not result.abstained and not result.search_only and not result.citations:
        problems.append("respuesta sin citas")
    outside = [c.chunk_id for c in result.citations if c.chunk_id not in retrieved]
    if outside:
        problems.append(f"citas a fragmentos no recuperados: {', '.join(outside)}")
    if case.expected_behavior == "abstain" and not result.abstained:
        problems.append("debía abstenerse y respondió")
    return problems


def fake_warnings(settings: Settings) -> list[str]:
    """In-process runs with a fake model do not accept Etapa 4: say so."""
    out = []
    if settings.llm_provider == "fake":
        out.append("LLM falso (LLM_PROVIDER=fake): respuestas simuladas; no acepta la Etapa 4.")
    if settings.embedding_provider == "fake":
        out.append("embeddings falsos (EMBEDDING_PROVIDER=fake): el ranking no significa nada.")
    return out


def api_answerer(base_url: str, settings: Settings) -> Callable[[str], Any]:
    """POST {base}/ask with {"question": ...}; eval runs send X-Eval-Key."""
    headers = {"X-Eval-Key": settings.eval_key} if settings.eval_key else {}
    client = httpx.Client(
        base_url=base_url.rstrip("/"), headers=headers, timeout=settings.llm_timeout_s * 3
    )

    def ask(question: str) -> Any:
        response = client.post("/ask", json={"question": question})
        response.raise_for_status()
        return response.json()

    return ask


def unreachable(exc: Exception) -> str:
    """Why a question could not be answered at all, in Spanish, with what to check."""
    hint = (
        "¿la API está levantada y acepta la clave?"
        if isinstance(exc, httpx.HTTPError)
        else "¿`make db`, `make migrate` y `make index`?"
    )
    reason = f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}"
    return f"no se pudo consultar el índice o la API ({reason}); {hint}"


def run_smoke(
    cases: list[SmokeCase],
    answerer: Callable[[str], Any],
    log: Callable[[str], None] = print,
    schema: type[BaseModel] = AskResult,
) -> int:
    """Number of failing questions."""
    failures = 0
    for case in cases:
        try:
            raw = answerer(case.question)
        except Exception as exc:  # any failure to answer is this question's failure
            log(f"FALLA {case.id}: {unreachable(exc)}")
            failures += 1
            continue
        try:
            data = raw.model_dump() if isinstance(raw, BaseModel) else raw
            result = schema.model_validate(data)
            problems = check_result(case, result)
        except ValidationError as exc:
            problems = [f"esquema inválido: {exc.error_count()} errores"]
            result = None
        status = "OK  " if not problems else "FALLA"
        what = "abstención" if result is not None and result.abstained else "respuesta"
        cites = len(result.citations) if result is not None else 0
        log(
            f"{status} {case.id}: {what}, {cites} citas"
            + ("" if not problems else " — " + "; ".join(problems))
        )
        failures += bool(problems)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test: 10 preguntas fijas.")
    parser.add_argument("--file", type=Path, default=SMOKE_FILE)
    parser.add_argument("--api", default=None, help="URL base del backend (si no, en proceso)")
    args = parser.parse_args(argv)
    s = get_settings()

    cases, errors = load_smoke(args.file)
    for error in errors:
        print(f"ERROR {error}")
    if errors or not cases:
        return 1
    if len(cases) != EXPECTED_CASES:
        print(f"AVISO {len(cases)} preguntas; el plan pide {EXPECTED_CASES}.")
    schema: type[BaseModel] = AskResult
    if args.api:
        from app.api import AskResponse  # what POST /ask returns

        answerer, schema = api_answerer(args.api, s), AskResponse
    else:
        from app.generation import answer

        answerer = answer
        for warning in fake_warnings(s):
            print(f"AVISO {warning}")
    failures = run_smoke(cases, answerer, schema=schema)
    print(f"{len(cases) - failures}/{len(cases)} preguntas pasan.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
