import json

import httpx
import pytest

from app.llm import (
    FAKE_MODEL_NAME,
    FAKE_TEXT,
    FakeLLM,
    LLMError,
    LLMResponse,
    LLMUnavailable,
    OpenAICompatLLM,
    config_problems,
    get_llm,
    provider_name,
)
from app.settings import Settings

GROQ = "https://api.groq.com/openai/v1"
WORKERS = "https://api.cloudflare.com/client/v4/accounts/abc/ai/v1"

SCHEMA = {
    "name": "answer",
    "schema": {
        "type": "object",
        "properties": {
            "answerable": {"type": "boolean"},
            "answer": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["answerable", "answer", "citations"],
        "additionalProperties": False,
    },
}
MESSAGES = [
    {"role": "system", "content": "Reglas del sistema."},
    {"role": "user", "content": "¿Qué es la virtud?"},
]


def llm_settings(**overrides) -> Settings:
    values = {
        "env": "local",
        "llm_provider": "openai_compat",
        "llm_base_url": GROQ,
        "llm_model": "openai/gpt-oss-20b",
        "llm_api_key": "primary-key",
        "llm_fallback_base_url": "",
        "llm_fallback_model": "",
        "llm_fallback_api_key": "",
        "llm_reasoning_effort": "low",
        "llm_timeout_s": 5.0,
        "output_max_tokens": 700,
    }
    values.update(overrides)
    return Settings(**values)


def with_fallback(**overrides) -> Settings:
    return llm_settings(
        llm_fallback_base_url=WORKERS,
        llm_fallback_model="@cf/openai/gpt-oss-20b",
        llm_fallback_api_key="fallback-key",
        **overrides,
    )


def completion(text: str, *, model: str = "openai/gpt-oss-20b", usage: dict | None = None):
    body = {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


class Recorder:
    """MockTransport handler: answers each request with the next scripted reply."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(request) if callable(reply) else reply

    def payload(self, i: int) -> dict:
        return json.loads(self.requests[i].content)

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


def client(settings: Settings, recorder: Recorder) -> OpenAICompatLLM:
    return get_llm(settings, transport=httpx.MockTransport(recorder))


# --- success and request shape ----------------------------------------------------------


def test_success_sends_schema_temperature_and_effort_and_parses_usage():
    rec = Recorder(
        completion(
            '{"answerable": true, "answer": "x [1]", "citations": [1]}',
            usage={
                "prompt_tokens": 812,
                "completion_tokens": 140,
                "completion_tokens_details": {"reasoning_tokens": 90},
            },
        )
    )
    response = client(llm_settings(), rec).complete(MESSAGES, json_schema=SCHEMA)

    assert response == LLMResponse(
        text='{"answerable": true, "answer": "x [1]", "citations": [1]}',
        model="openai/gpt-oss-20b",
        provider="groq",
        tokens_in=812,
        tokens_out=140,
        reasoning_tokens=90,
        finish_reason="stop",
    )
    request = rec.requests[0]
    assert str(request.url) == f"{GROQ}/chat/completions"
    assert request.headers["authorization"] == "Bearer primary-key"
    payload = rec.payload(0)
    assert payload["model"] == "openai/gpt-oss-20b"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 700  # settings.output_max_tokens
    assert payload["reasoning_effort"] == "low"
    assert payload["messages"] == MESSAGES
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "answer"
    assert payload["response_format"]["json_schema"]["schema"] == SCHEMA["schema"]
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_explicit_max_tokens_and_no_schema_and_no_effort():
    rec = Recorder(completion("hola"))
    llm = client(llm_settings(llm_reasoning_effort=""), rec)
    llm.complete(MESSAGES, max_tokens=50)
    payload = rec.payload(0)
    assert payload["max_tokens"] == 50
    assert "reasoning_effort" not in payload
    assert "response_format" not in payload


def test_bare_json_schema_gets_a_name():
    rec = Recorder(completion("{}"))
    client(llm_settings(), rec).complete(MESSAGES, json_schema=SCHEMA["schema"])
    spec = rec.payload(0)["response_format"]["json_schema"]
    assert spec["name"] == "response"
    assert spec["schema"] == SCHEMA["schema"]


def test_missing_usage_counts_zero_and_model_falls_back_to_requested():
    body = {"choices": [{"message": {"content": "ok"}}]}
    rec = Recorder(httpx.Response(200, json=body))
    response = client(llm_settings(), rec).complete(MESSAGES)
    assert (response.text, response.tokens_in, response.tokens_out) == ("ok", 0, 0)
    assert response.model == "openai/gpt-oss-20b"
    assert response.reasoning_tokens is None


def test_null_content_is_empty_text():
    body = {
        "model": "m",
        "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 700},
    }
    response = client(llm_settings(), Recorder(httpx.Response(200, json=body))).complete(MESSAGES)
    assert response.text == ""
    assert response.finish_reason == "length"
    assert response.tokens_out == 700


# --- structured-output fallback ---------------------------------------------------------


def rejected(what: str) -> httpx.Response:
    return httpx.Response(
        400, json={"error": {"message": f"response_format `{what}` is not supported"}}
    )


def test_json_schema_rejected_falls_back_to_json_object_and_remembers():
    rec = Recorder(rejected("json_schema"), completion("{}"), completion("{}"))
    llm = client(llm_settings(), rec)
    llm.complete(MESSAGES, json_schema=SCHEMA)
    assert rec.payload(0)["response_format"]["type"] == "json_schema"
    second = rec.payload(1)
    assert second["response_format"] == {"type": "json_object"}
    system = second["messages"][0]["content"]
    assert system.startswith("Reglas del sistema.")
    assert "JSON" in system and '"answerable"' in system  # the schema goes in the prompt
    assert len(second["messages"]) == 2  # instruction merged into the one system message

    llm.complete(MESSAGES, json_schema=SCHEMA)  # remembered: no second rejection
    assert len(rec.requests) == 3
    assert rec.payload(2)["response_format"] == {"type": "json_object"}


def test_both_formats_rejected_falls_back_to_plain_instructions():
    rec = Recorder(rejected("json_schema"), rejected("json_object"), completion("{}"))
    client(llm_settings(), rec).complete(
        [{"role": "user", "content": "pregunta"}], json_schema=SCHEMA
    )
    last = rec.payload(2)
    assert "response_format" not in last
    assert last["messages"][0]["role"] == "system"  # added first when there was none
    assert "esquema JSON" in last["messages"][0]["content"]
    assert last["messages"][1] == {"role": "user", "content": "pregunta"}


def test_other_400_is_a_configuration_error_and_does_not_use_fallback():
    rec = Recorder(httpx.Response(400, json={"error": {"message": "model not found"}}))
    with pytest.raises(LLMError) as err:
        client(with_fallback(), rec).complete(MESSAGES, json_schema=SCHEMA)
    assert not isinstance(err.value, LLMUnavailable)
    assert err.value.status_code == 400
    assert rec.hosts() == ["api.groq.com"]


def test_rejected_reasoning_effort_is_dropped():
    rec = Recorder(
        httpx.Response(400, json={"error": {"message": "unknown field reasoning_effort"}}),
        completion("ok"),
    )
    client(llm_settings(), rec).complete(MESSAGES)
    assert rec.payload(0)["reasoning_effort"] == "low"
    assert "reasoning_effort" not in rec.payload(1)


def test_groq_json_validate_failed_returns_the_failed_generation():
    rec = Recorder(
        httpx.Response(
            400,
            json={
                "error": {
                    "message": "Failed to validate JSON",
                    "code": "json_validate_failed",
                    "failed_generation": '{"answerable": tru',
                }
            },
        )
    )
    response = client(with_fallback(), rec).complete(MESSAGES, json_schema=SCHEMA)
    assert response.text == '{"answerable": tru'
    assert response.finish_reason == "json_validate_failed"
    assert len(rec.requests) == 1


# --- fallback provider ------------------------------------------------------------------


def test_429_goes_to_the_fallback_provider_once():
    rec = Recorder(
        httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "rate"}}),
        completion("ok", model="@cf/openai/gpt-oss-20b", usage={"prompt_tokens": 5}),
    )
    response = client(with_fallback(), rec).complete(MESSAGES, json_schema=SCHEMA)
    assert rec.hosts() == ["api.groq.com", "api.cloudflare.com"]
    assert str(rec.requests[1].url) == f"{WORKERS}/chat/completions"
    assert rec.requests[1].headers["authorization"] == "Bearer fallback-key"
    assert rec.payload(1)["model"] == "@cf/openai/gpt-oss-20b"
    assert response.provider == "workers_ai"
    assert response.model == "@cf/openai/gpt-oss-20b"
    assert response.tokens_in == 5


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(503, text="overloaded"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("refused"),
    ],
)
def test_5xx_timeouts_and_network_errors_use_the_fallback(failure):
    rec = Recorder(failure, completion("ok"))
    response = client(with_fallback(), rec).complete(MESSAGES)
    assert response.text == "ok"
    assert rec.hosts() == ["api.groq.com", "api.cloudflare.com"]


def test_both_down_raises_llm_unavailable():
    rec = Recorder(httpx.Response(500, text="boom"), httpx.ReadTimeout("slow"))
    with pytest.raises(LLMUnavailable) as err:
        client(with_fallback(), rec).complete(MESSAGES)
    assert "groq" in str(err.value) and "workers_ai" in str(err.value)
    assert len(rec.requests) == 2


def test_no_fallback_429_raises_llm_unavailable_with_status_and_retry_after():
    rec = Recorder(httpx.Response(429, headers={"retry-after": "12"}, text="slow down"))
    with pytest.raises(LLMUnavailable) as err:
        client(llm_settings(), rec).complete(MESSAGES)
    assert err.value.status_code == 429  # evals' CachingLLM backs off on this
    assert err.value.retry_after == 12.0
    assert len(rec.requests) == 1


def test_rate_limit_on_either_provider_is_reported_as_429():
    rec = Recorder(
        httpx.Response(429, headers={"retry-after": "3"}, text="slow down"),
        httpx.Response(503, text="overloaded"),
    )
    with pytest.raises(LLMUnavailable) as err:
        client(with_fallback(), rec).complete(MESSAGES)
    assert (err.value.status_code, err.value.retry_after) == (429, 3.0)


def test_malformed_body_counts_as_a_provider_failure():
    rec = Recorder(httpx.Response(200, json={"nope": 1}), completion("ok"))
    assert client(with_fallback(), rec).complete(MESSAGES).text == "ok"


# --- fake -------------------------------------------------------------------------------


def test_fake_returns_the_script_in_order_and_records_calls():
    boom = LLMUnavailable("down")
    scripted = LLMResponse(text="t", model="m", provider="p", tokens_in=1, tokens_out=2)
    llm = FakeLLM(["primera respuesta", scripted, boom, lambda messages: "de una función"])
    first = llm.complete(MESSAGES, json_schema=SCHEMA, max_tokens=10)
    assert first.text == "primera respuesta"
    assert first.provider == "fake" and first.model == FAKE_MODEL_NAME
    assert first.tokens_in == 7 and first.tokens_out == 2  # word counts
    assert llm.complete(MESSAGES) is scripted
    with pytest.raises(LLMUnavailable):
        llm.complete(MESSAGES)
    assert llm.complete(MESSAGES).text == "de una función"
    with pytest.raises(AssertionError):
        llm.complete(MESSAGES)
    assert len(llm.calls) == 5
    assert llm.calls[0]["json_schema"] is SCHEMA and llm.calls[0]["max_tokens"] == 10


def test_fake_without_script_answers_placeholder_json_that_fits_the_schema():
    llm = FakeLLM()
    data = json.loads(llm.complete(MESSAGES, json_schema=SCHEMA).text)
    assert data == {"answerable": True, "answer": FAKE_TEXT, "citations": [1]}
    verdict = {"type": "object", "properties": {"verdict": {"enum": ["pass", "fail"]}}}
    assert json.loads(llm.complete(MESSAGES, json_schema=verdict).text) == {"verdict": "pass"}
    assert llm.complete(MESSAGES).text == FAKE_TEXT


# --- factory and config -----------------------------------------------------------------


def test_get_llm_selects_fake():
    assert isinstance(get_llm(llm_settings(llm_provider="fake")), FakeLLM)


def test_get_llm_builds_primary_and_fallback():
    llm = get_llm(with_fallback())
    assert isinstance(llm, OpenAICompatLLM)
    assert (llm.provider, llm.model) == ("groq", "openai/gpt-oss-20b")
    assert llm.fallback.name == "workers_ai"
    assert get_llm(llm_settings()).fallback is None


def test_get_llm_rejects_bad_config():
    with pytest.raises(LLMError):
        get_llm(llm_settings(llm_provider="nope"))
    with pytest.raises(LLMError):
        get_llm(llm_settings(llm_base_url=""))


def test_provider_names():
    assert provider_name(GROQ) == "groq"
    assert provider_name(WORKERS) == "workers_ai"
    assert provider_name("http://localhost:11434/v1") == "ollama"
    assert provider_name("https://api.deepinfra.com/v1/openai") == "api.deepinfra.com"


def test_config_problems():
    assert config_problems(llm_settings()) == []
    assert config_problems(llm_settings(llm_api_key="")) == ["falta LLM_API_KEY"]
    ollama = llm_settings(llm_base_url="http://localhost:11434/v1", llm_api_key="")
    assert config_problems(ollama) == []  # local Ollama needs no key
    assert config_problems(llm_settings(llm_model="")) == ["falta LLM_BASE_URL o LLM_MODEL"]
    assert config_problems(llm_settings(llm_fallback_base_url=WORKERS)) == [
        "LLM_FALLBACK_BASE_URL sin LLM_FALLBACK_MODEL"
    ]
    assert config_problems(llm_settings(llm_provider="fake")) == []
    assert config_problems(llm_settings(llm_provider="fake", env="prod")) != []
    assert config_problems(llm_settings(llm_provider="x")) != []
