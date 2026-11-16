import pytest

from app.settings import Settings
from evals.cost import (
    CachingLLM,
    CostLimitExceeded,
    QuotaExhausted,
    ResponseCache,
    estimate_cost,
    guard,
    is_rate_limit,
)
from evals.judge import max_judge_calls
from evals.schema import Case
from tests.test_evals_support import abstain_case, case


def settings(**kw) -> Settings:
    values = {
        "context_max_tokens": 2000,
        "output_max_tokens": 700,
        "eval_prompt_overhead_tokens": 300,
        "eval_judge_prompt_tokens": 500,
        "eval_judge_max_tokens": 300,
        "eval_llm_usd_per_mtok_in": 0.1,
        "eval_llm_usd_per_mtok_out": 0.5,
        "eval_judge_usd_per_mtok_in": 1.0,
        "eval_judge_usd_per_mtok_out": 2.0,
        "eval_max_usd": 1.0,
    }
    values.update(kw)
    return Settings(**values)


CASES = [
    Case.model_validate(case("a")),  # faithfulness + key_points
    Case.model_validate(case("b", type="false_premise", expected_behavior="correct_premise")),
    Case.model_validate(abstain_case("c")),  # faithfulness only if it answers
]


def test_estimate_cost():
    est = estimate_cost(CASES, settings(), judge_calls_per_case=max_judge_calls)
    assert est.generator_calls == 3
    assert (est.generator_tokens_in, est.generator_tokens_out) == (3 * 2300, 3 * 700)
    assert est.judge_calls == 2 + 3 + 1
    assert est.judge_tokens_in == 6 * (500 + 2000 + 700)
    assert est.judge_tokens_out == 6 * 300
    assert est.usd_generator == pytest.approx((6900 * 0.1 + 2100 * 0.5) / 1e6)
    assert est.usd_judge == pytest.approx((19200 * 1.0 + 1800 * 2.0) / 1e6)
    assert "USD" in est.describe()
    no_ctx = estimate_cost(CASES, settings(), with_context=False)
    assert no_ctx.generator_tokens_in == 3 * 300 and no_ctx.judge_calls == 0


def test_guard_aborts_above_the_limit():
    est = estimate_cost(CASES * 100, settings(), judge_calls_per_case=max_judge_calls)
    guard(est, settings(eval_max_usd=est.usd + 0.01))
    with pytest.raises(CostLimitExceeded, match="EVAL_MAX_USD"):
        guard(est, settings(eval_max_usd=est.usd / 2))


class Response:
    def __init__(self, text):
        self.text, self.model, self.provider, self.tokens_in, self.tokens_out = (
            text,
            "m",
            "p",
            10,
            2,
        )


class ProviderError(Exception):
    def __init__(self, status_code=429, retry_after=None):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


class Inner:
    provider = "groq"
    model = "m"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages, *, json_schema=None, max_tokens, temperature=0):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return Response(item)


def caching(inner, tmp_path, **kw):
    sleeps = []
    llm = CachingLLM(
        inner,
        model_id="m",
        cache=ResponseCache(tmp_path),
        max_retries=kw.get("max_retries", 3),
        backoff_s=1.0,
        backoff_max_s=5.0,
        sleep=sleeps.append,
    )
    return llm, sleeps


MSG = [{"role": "user", "content": "hola"}]


def test_cache_hit_and_resume(tmp_path):
    llm, _ = caching(Inner(["uno", "dos"]), tmp_path)
    assert llm.complete(MSG, max_tokens=10).text == "uno"
    # The same request again in the same run is a new call (e.g. a retry after bad JSON).
    assert llm.complete(MSG, max_tokens=10).text == "dos"
    assert (llm.hits, llm.misses) == (0, 2)
    # A resumed run replays both slots from disk without calling the provider.
    inner = Inner([])
    again, _ = caching(inner, tmp_path)
    assert again.complete(MSG, max_tokens=10).text == "uno"
    assert again.complete(MSG, max_tokens=10).text == "dos"
    assert inner.calls == 0 and again.hits == 2
    # Other inputs miss.
    other, _ = caching(Inner(["tres"]), tmp_path)
    assert other.complete(MSG, max_tokens=11).text == "tres"


def test_cache_key_includes_the_provider(tmp_path):
    llm, _ = caching(Inner(["real"]), tmp_path)
    llm.complete(MSG, max_tokens=10)
    fake = Inner(["simulated"])
    fake.provider = "fake"
    other, _ = caching(fake, tmp_path)
    assert other.complete(MSG, max_tokens=10).text == "simulated"


def test_retries_on_429_with_backoff(tmp_path):
    inner = Inner([ProviderError(), ProviderError(retry_after=4), "ok"])
    llm, sleeps = caching(inner, tmp_path)
    assert llm.complete(MSG, max_tokens=10).text == "ok"
    assert sleeps == [1.0, 4.0]  # 1 s, then max(2 s, Retry-After 4 s)
    assert not llm.exhausted


def test_quota_exhausted_after_all_retries(tmp_path):
    inner = Inner([ProviderError()] * 3)
    llm, sleeps = caching(inner, tmp_path, max_retries=2)
    with pytest.raises(QuotaExhausted, match="reanuda desde la caché"):
        llm.complete(MSG, max_tokens=10)
    assert llm.exhausted and sleeps == [1.0, 2.0]
    assert list(tmp_path.rglob("*.json")) == []  # failures are not cached


def test_other_errors_are_not_retried(tmp_path):
    llm, sleeps = caching(Inner([ProviderError(status_code=401)]), tmp_path)
    with pytest.raises(ProviderError):
        llm.complete(MSG, max_tokens=10)
    assert sleeps == []


def test_is_rate_limit():
    assert is_rate_limit(ProviderError())
    assert not is_rate_limit(ProviderError(status_code=500))
    assert not is_rate_limit(ValueError("boom"))

    class Wrapped(Exception):
        response = type("R", (), {"status_code": 429})()

    assert is_rate_limit(Wrapped())

    class RateLimitError(Exception):  # e.g. an SDK error class, without a status attribute
        pass

    assert is_rate_limit(RateLimitError())
