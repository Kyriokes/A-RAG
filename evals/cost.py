"""Cost guard, response cache and retries for eval runs that call an LLM.

PLAN, "Costos y topes": before a run with LLM the runner prints the estimated tokens and
USD and aborts above EVAL_MAX_USD; generator and judge responses are cached on disk; 429s
are retried with backoff, and a run cut short by the quota resumes from the cache when
the same command is run again.

Prices live in settings (eval_*_usd_per_mtok_*) and are "a confirmar". The estimate is an
upper bound per call (full context, full output) and does not discount cached responses.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.settings import Settings
from evals.schema import Case

CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "llm"


class CostLimitExceeded(RuntimeError):
    pass


class QuotaExhausted(RuntimeError):
    """The provider kept answering 429 after every retry. Re-run to resume from cache."""


# --- estimate --------------------------------------------------------------------------


@dataclass(frozen=True)
class CostEstimate:
    generator_calls: int
    generator_tokens_in: int
    generator_tokens_out: int
    judge_calls: int
    judge_tokens_in: int
    judge_tokens_out: int
    usd_generator: float
    usd_judge: float

    @property
    def usd(self) -> float:
        return self.usd_generator + self.usd_judge

    @property
    def tokens(self) -> int:
        return (
            self.generator_tokens_in
            + self.generator_tokens_out
            + self.judge_tokens_in
            + self.judge_tokens_out
        )

    def describe(self) -> str:
        return (
            f"Estimación (cota superior, sin descontar la caché): "
            f"{self.generator_calls} llamadas al generador "
            f"({self.generator_tokens_in:,} tokens de entrada, {self.generator_tokens_out:,} de "
            f"salida) y {self.judge_calls} al juez ({self.judge_tokens_in:,} + "
            f"{self.judge_tokens_out:,}). Total {self.tokens:,} tokens, "
            f"{self.usd:.4f} USD a precios de settings (a confirmar)."
        )

    def to_json(self) -> dict:
        return {**self.__dict__, "usd": self.usd, "tokens": self.tokens}


def _usd(tokens_in: int, tokens_out: int, price_in: float, price_out: float) -> float:
    return tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6


def estimate_cost(
    cases: Sequence[Case],
    settings: Settings,
    *,
    judge_calls_per_case: Callable[[Case], int] | None = None,
    with_context: bool = True,
) -> CostEstimate:
    """Tokens and USD for one generator call per case plus the judge calls.

    Generator: prompt overhead + context (CONTEXT_MAX_TOKENS) in, OUTPUT_MAX_TOKENS out.
    Judge: rubric + context + the answer in, eval_judge_max_tokens out.
    """
    n = len(cases)
    gen_in = n * (
        settings.eval_prompt_overhead_tokens + (settings.context_max_tokens if with_context else 0)
    )
    gen_out = n * settings.output_max_tokens
    calls = sum(judge_calls_per_case(c) for c in cases) if judge_calls_per_case else 0
    per_judge_in = (
        settings.eval_judge_prompt_tokens + settings.context_max_tokens + settings.output_max_tokens
    )
    judge_in = calls * per_judge_in
    judge_out = calls * settings.eval_judge_max_tokens
    return CostEstimate(
        generator_calls=n,
        generator_tokens_in=gen_in,
        generator_tokens_out=gen_out,
        judge_calls=calls,
        judge_tokens_in=judge_in,
        judge_tokens_out=judge_out,
        usd_generator=_usd(
            gen_in,
            gen_out,
            settings.eval_llm_usd_per_mtok_in,
            settings.eval_llm_usd_per_mtok_out,
        ),
        usd_judge=_usd(
            judge_in,
            judge_out,
            settings.eval_judge_usd_per_mtok_in,
            settings.eval_judge_usd_per_mtok_out,
        ),
    )


def guard(estimate: CostEstimate, settings: Settings) -> None:
    """Raise CostLimitExceeded when the estimate is above EVAL_MAX_USD."""
    if estimate.usd > settings.eval_max_usd:
        raise CostLimitExceeded(
            f"La estimación ({estimate.usd:.4f} USD) supera EVAL_MAX_USD "
            f"({settings.eval_max_usd:g} USD). No se corre nada. Subir EVAL_MAX_USD solo "
            "con acuerdo de la persona, o correr menos casos."
        )


# --- cache and retries -----------------------------------------------------------------


class ResponseCache:
    """One JSON file per response under `directory`, named by its sha256 key."""

    def __init__(self, directory: Path = CACHE_DIR) -> None:
        self.directory = directory

    @staticmethod
    def key(model: str, prompt: Any, inputs: Any) -> str:
        payload = json.dumps(
            {"model": model, "prompt": prompt, "inputs": inputs},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None  # a torn write: call again

    def put(self, key: str, data: dict) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


@dataclass
class CachedResponse:
    """Stand-in with the fields of app.llm.LLMResponse, used if that class is missing."""

    text: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int


_RESPONSE_FIELDS = ("text", "model", "provider", "tokens_in", "tokens_out")


def _response_from(data: dict) -> Any:
    fields = {k: data.get(k) for k in _RESPONSE_FIELDS}
    try:
        from app.llm import LLMResponse

        return LLMResponse(**fields)
    except Exception:  # module not there yet, or a different constructor
        return CachedResponse(**fields)


def is_rate_limit(exc: BaseException) -> bool:
    """429 from any client: a status attribute on the error or its response, or the name."""
    for obj in (exc, getattr(exc, "response", None)):
        if obj is not None and 429 in (
            getattr(obj, "status_code", None),
            getattr(obj, "status", None),
        ):
            return True
    name = type(exc).__name__.lower()
    return "ratelimit" in name or "rate_limit" in name or " 429" in f" {exc}"


class CachingLLM:
    """Wraps an app.llm LLM: on-disk cache keyed by sha256(model, prompt, inputs) and
    retries with exponential backoff on 429.

    The n-th identical request of a run gets its own cache slot, so a retry the caller
    makes after an invalid answer is a new call, and a resumed run replays the same slots.
    """

    def __init__(
        self,
        inner: Any,
        *,
        model_id: str,
        cache: ResponseCache,
        max_retries: int,
        backoff_s: float,
        backoff_max_s: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self.model_id = model_id
        # The cache key names the client's provider and model, so responses of a fake or
        # a different provider are never replayed as the configured model's.
        self._key_model = (
            f"{getattr(inner, 'provider', '')}|{getattr(inner, 'model', '') or model_id}"
        )
        self.cache = cache
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.backoff_max_s = backoff_max_s
        self._sleep = sleep
        self._seen: dict[str, int] = {}
        self.hits = 0
        self.misses = 0
        self.exhausted = False

    @classmethod
    def from_settings(
        cls, inner: Any, *, model_id: str, settings: Settings, cache: ResponseCache
    ) -> CachingLLM:
        return cls(
            inner,
            model_id=model_id,
            cache=cache,
            max_retries=settings.eval_max_retries,
            backoff_s=settings.eval_backoff_s,
            backoff_max_s=settings.eval_backoff_max_s,
        )

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    def complete(
        self,
        messages: list[dict],
        *,
        json_schema: dict | None = None,
        max_tokens: int,
        temperature: float = 0,
    ) -> Any:
        inputs = {"json_schema": json_schema, "max_tokens": max_tokens, "temperature": temperature}
        base = ResponseCache.key(self._key_model, messages, inputs)
        occurrence = self._seen.get(base, 0)
        self._seen[base] = occurrence + 1
        key = ResponseCache.key(self._key_model, messages, {**inputs, "occurrence": occurrence})
        cached = self.cache.get(key)
        if cached is not None:
            self.hits += 1
            return _response_from(cached)
        response = self._call(
            messages, json_schema=json_schema, max_tokens=max_tokens, temperature=temperature
        )
        self.misses += 1
        self.cache.put(key, {k: getattr(response, k, None) for k in _RESPONSE_FIELDS})
        return response

    def _call(self, messages: list[dict], **kwargs: Any) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return self._inner.complete(messages, **kwargs)
            except Exception as exc:
                if not is_rate_limit(exc):
                    raise
                if attempt == self.max_retries:
                    self.exhausted = True
                    raise QuotaExhausted(
                        f"El proveedor sigue respondiendo 429 tras {self.max_retries} "
                        "reintentos. Volver a correr el mismo comando reanuda desde la caché."
                    ) from exc
                delay = min(self.backoff_s * 2**attempt, self.backoff_max_s)
                retry_after = getattr(exc, "retry_after", None)
                if isinstance(retry_after, int | float):
                    delay = max(delay, min(float(retry_after), self.backoff_max_s))
                self._sleep(delay)
        raise AssertionError("unreachable")
