"""LLM clients: one OpenAI-compatible chat client and a scripted fake (PLAN, "Stack").

Groq, Cloudflare Workers AI and Ollama all serve `gpt-oss-20b` behind an OpenAI-compatible
`POST {base}/chat/completions`, so switching provider is switching base URL, model id and
key (rule 5). The client:

- sends temperature 0, `max_tokens` (settings.output_max_tokens by default, reasoning
  included) and `reasoning_effort` when set;
- asks for structured output with `response_format: json_schema` when the caller passes a
  schema. If the provider rejects that (HTTP 400/422 naming the response format), it falls
  back to `json_object` and then to plain instructions in the system message, and remembers
  per provider what worked;
- on 429, 5xx, timeouts and network errors tries the fallback provider once (no waiting:
  the demo prefers a fast answer from the backup over a slow one from the primary), and
  raises LLMUnavailable when both fail. Other HTTP errors (bad key, unknown model) raise
  LLMError straight away: they are configuration problems, not outages.

FakeLLM returns scripted responses for tests. Selected with LLM_PROVIDER=fake without a
script, it answers with placeholder JSON that fits the requested schema and says it is
simulated; it never stands in for a real model's output.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.settings import Settings, get_settings

log = logging.getLogger(__name__)

FAKE_MODEL_NAME = "fake/scripted-llm"
FAKE_TEXT = "Texto simulado por FakeLLM (sin modelo real)."

# Structured-output modes, from strictest to loosest.
_FORMATS = ("json_schema", "json_object", "prompt")
_FORMAT_ERROR_WORDS = ("response_format", "json_schema", "json_object", "structured", "schema")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


@dataclass
class LLMResponse:
    text: str
    model: str  # model id the provider reports having served
    provider: str  # "groq", "workers_ai", "ollama", "fake" or the host name
    tokens_in: int = 0
    tokens_out: int = 0  # completion tokens, reasoning included
    reasoning_tokens: int | None = None  # when the provider reports them
    finish_reason: str = ""


class LLMError(RuntimeError):
    """The LLM call failed. The message is for logs, never shown verbatim to the public."""

    def __init__(
        self, message: str, *, status_code: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class LLMUnavailable(LLMError):
    """Every configured provider failed with 429, 5xx, a timeout or a network error."""


class _Retryable(LLMError):
    """One provider failed in a way that justifies trying the fallback."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key: str = ""


def provider_name(base_url: str) -> str:
    """Short provider name from the base URL, for logs and AskResult.provider."""
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if "groq" in host:
        return "groq"
    if "cloudflare" in host:
        return "workers_ai"
    if parsed.port == 11434 or "ollama" in host:
        return "ollama"
    return host or "openai_compat"


def _response_format(json_schema: dict) -> dict:
    """OpenAI's json_schema object. Accepts it ready-made ({"name", "schema", ...}) or a bare
    JSON schema, which gets the name "response"."""
    if "schema" in json_schema and isinstance(json_schema["schema"], dict):
        spec = {"strict": True, **json_schema}
    else:
        spec = {"name": "response", "schema": json_schema, "strict": True}
    return {"type": "json_schema", "json_schema": spec}


def _schema_instruction(json_schema: dict) -> str:
    schema = json_schema.get("schema", json_schema)
    return (
        "Responde únicamente con un objeto JSON válido, sin texto antes ni después, que "
        f"cumpla este esquema JSON: {json.dumps(schema, ensure_ascii=False)}"
    )


def _with_instruction(messages: list[dict], instruction: str) -> list[dict]:
    """Messages with `instruction` added to the first system message (or a new one first).
    Some chat templates accept a single system message, at the start."""
    out = [dict(m) for m in messages]
    for m in out:
        if m.get("role") == "system":
            m["content"] = f"{m.get('content', '')}\n\n{instruction}"
            return out
    return [{"role": "system", "content": instruction}, *out]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "")
    try:
        return float(value)
    except ValueError:
        return None


def _error_payload(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    error = data.get("error") if isinstance(data, dict) else None
    return error if isinstance(error, dict) else {}


class OpenAICompatLLM:
    """Chat client for any OpenAI-compatible endpoint, with one fallback provider."""

    def __init__(
        self,
        primary: ProviderConfig,
        fallback: ProviderConfig | None = None,
        *,
        timeout_s: float = 30.0,
        reasoning_effort: str = "",
        default_max_tokens: int = 700,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.reasoning_effort = reasoning_effort
        self.default_max_tokens = default_max_tokens
        self._client = httpx.Client(timeout=timeout_s, transport=transport)
        # Per base URL: index into _FORMATS of the mode the provider accepted, and whether
        # it rejected reasoning_effort.
        self._format: dict[str, int] = {}
        self._no_reasoning: set[str] = set()

    @property
    def model(self) -> str:
        return self.primary.model

    @property
    def provider(self) -> str:
        return self.primary.name

    def complete(
        self,
        messages: list[dict],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        temperature: float = 0,
    ) -> LLMResponse:
        max_tokens = max_tokens or self.default_max_tokens
        providers = [self.primary] + ([self.fallback] if self.fallback else [])
        failures: list[_Retryable] = []
        for provider in providers:
            try:
                return self._complete_with(provider, messages, json_schema, max_tokens, temperature)
            except _Retryable as exc:
                log.warning("LLM %s falló: %s", provider.name, exc)
                failures.append(exc)
        # Report a 429 when any provider rate-limited, so callers that back off on 429 (the
        # eval runner) wait and retry instead of recording an outage.
        limited = [f for f in failures if f.status_code == 429]
        cause = limited[-1] if limited else failures[-1]
        raise LLMUnavailable(
            "Ningún proveedor de LLM respondió: " + "; ".join(str(f) for f in failures),
            status_code=cause.status_code,
            retry_after=cause.retry_after,
        )

    def _payload(
        self,
        provider: ProviderConfig,
        messages: list[dict],
        json_schema: dict | None,
        mode: str | None,
        max_tokens: int,
        temperature: float,
    ) -> dict:
        payload: dict = {
            "model": provider.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.reasoning_effort and provider.base_url not in self._no_reasoning:
            payload["reasoning_effort"] = self.reasoning_effort
        if json_schema is not None:
            if mode == "json_schema":
                payload["response_format"] = _response_format(json_schema)
            else:
                payload["messages"] = _with_instruction(messages, _schema_instruction(json_schema))
                if mode == "json_object":
                    payload["response_format"] = {"type": "json_object"}
        return payload

    def _complete_with(
        self,
        provider: ProviderConfig,
        messages: list[dict],
        json_schema: dict | None,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
        while True:
            mode = _FORMATS[self._format.get(provider.base_url, 0)] if json_schema else None
            payload = self._payload(provider, messages, json_schema, mode, max_tokens, temperature)
            try:
                response = self._client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise _Retryable(f"{provider.name}: sin respuesta a tiempo") from exc
            except httpx.HTTPError as exc:
                raise _Retryable(f"{provider.name}: error de red ({exc})") from exc

            status = response.status_code
            if status == 429 or status >= 500:
                raise _Retryable(
                    f"{provider.name}: HTTP {status}",
                    status_code=status,
                    retry_after=_retry_after(response),
                )
            if status in (400, 422):
                error = _error_payload(response)
                if error.get("code") == "json_validate_failed":
                    # Groq checked the output against the format and it failed. That is an
                    # invalid answer, not an outage: hand it to the caller, who validates.
                    return LLMResponse(
                        text=str(error.get("failed_generation") or ""),
                        model=provider.model,
                        provider=provider.name,
                        finish_reason="json_validate_failed",
                    )
                body = response.text.lower()
                if mode in ("json_schema", "json_object") and any(
                    word in body for word in _FORMAT_ERROR_WORDS
                ):
                    self._format[provider.base_url] = _FORMATS.index(mode) + 1
                    log.info("LLM %s rechaza response_format=%s; se degrada", provider.name, mode)
                    continue
                if "reasoning" in body and "reasoning_effort" in payload:
                    self._no_reasoning.add(provider.base_url)
                    log.info("LLM %s rechaza reasoning_effort; se omite", provider.name)
                    continue
            if status >= 400:
                raise LLMError(
                    f"{provider.name}: HTTP {status}: {response.text[:300]}", status_code=status
                )
            return self._parse(provider, response)

    @staticmethod
    def _parse(provider: ProviderConfig, response: httpx.Response) -> LLMResponse:
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _Retryable(f"{provider.name}: respuesta sin choices[0].message") from exc
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return LLMResponse(
            text=message.get("content") or "",
            model=data.get("model") or provider.model,
            provider=provider.name,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            reasoning_tokens=details.get("reasoning_tokens"),
            finish_reason=choice.get("finish_reason") or "",
        )


# --- fake --------------------------------------------------------------------------------

Scripted = str | LLMResponse | BaseException | Callable[[list[dict]], "str | LLMResponse"]


def _placeholder(schema: dict) -> object:
    """Smallest value that fits a JSON schema, with self-describing placeholder text."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        props = schema.get("properties") or {}
        return {name: _placeholder(sub) for name, sub in props.items()}
    if kind == "array":
        return [_placeholder(schema.get("items") or {"type": "string"})]
    if kind == "boolean":
        return True
    if kind in ("integer", "number"):
        return 1
    if kind == "null":
        return None
    return FAKE_TEXT


def _count_words(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", "")).split()) for m in messages)


class FakeLLM:
    """Scripted LLM for tests and offline runs.

    `responses` is consumed in order: a string becomes the response text, an LLMResponse is
    returned as is, an exception is raised, and a callable gets the messages and returns
    either. Without a script it returns placeholder JSON that fits `json_schema` (or a
    placeholder sentence), always labelled as simulated. Every call is kept in `calls`.
    Token counts are word counts: deterministic, not real.
    """

    provider = "fake"
    model = FAKE_MODEL_NAME

    def __init__(self, responses: list[Scripted] | None = None) -> None:
        self._script = list(responses) if responses is not None else None
        self.calls: list[dict] = []

    def complete(
        self,
        messages: list[dict],
        *,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        temperature: float = 0,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": messages,
                "json_schema": json_schema,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self._script is None:
            schema = (json_schema or {}).get("schema", json_schema)
            item: Scripted = json.dumps(_placeholder(schema)) if schema else FAKE_TEXT
        elif not self._script:
            raise AssertionError("FakeLLM: no quedan respuestas en el guion")
        else:
            item = self._script.pop(0)
        if callable(item) and not isinstance(item, BaseException):
            item = item(messages)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return LLMResponse(
            text=str(item),
            model=FAKE_MODEL_NAME,
            provider="fake",
            tokens_in=_count_words(messages),
            tokens_out=len(str(item).split()),
            finish_reason="stop",
        )


# --- factory -----------------------------------------------------------------------------


def _is_local(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "").lower() in _LOCAL_HOSTS


def config_problems(settings: Settings | None = None) -> list[str]:
    """What is missing for get_llm() to build a usable client (for /readyz)."""
    s = settings or get_settings()
    if s.llm_provider == "fake":
        return ["LLM_PROVIDER=fake en producción"] if s.env == "prod" else []
    if s.llm_provider != "openai_compat":
        return [f"LLM_PROVIDER desconocido: {s.llm_provider!r} (opciones: openai_compat, fake)"]
    problems = []
    if not s.llm_base_url or not s.llm_model:
        problems.append("falta LLM_BASE_URL o LLM_MODEL")
    elif not s.llm_api_key and not _is_local(s.llm_base_url):
        problems.append("falta LLM_API_KEY")
    if s.llm_fallback_base_url and not s.llm_fallback_model:
        problems.append("LLM_FALLBACK_BASE_URL sin LLM_FALLBACK_MODEL")
    return problems


def get_llm(settings: Settings | None = None, *, transport: httpx.BaseTransport | None = None):
    """LLM selected by settings.llm_provider: "openai_compat" (default) or "fake"."""
    s = settings or get_settings()
    if s.llm_provider == "fake":
        return FakeLLM()
    if s.llm_provider != "openai_compat":
        raise LLMError(f"LLM_PROVIDER desconocido: {s.llm_provider!r} (openai_compat, fake).")
    if not s.llm_base_url or not s.llm_model:
        raise LLMError("Falta LLM_BASE_URL o LLM_MODEL.")
    primary = ProviderConfig(
        provider_name(s.llm_base_url), s.llm_base_url, s.llm_model, s.llm_api_key
    )
    fallback = None
    if s.llm_fallback_base_url and s.llm_fallback_model:
        fallback = ProviderConfig(
            provider_name(s.llm_fallback_base_url),
            s.llm_fallback_base_url,
            s.llm_fallback_model,
            s.llm_fallback_api_key,
        )
    return OpenAICompatLLM(
        primary,
        fallback,
        timeout_s=s.llm_timeout_s,
        reasoning_effort=s.llm_reasoning_effort,
        default_max_tokens=s.output_max_tokens,
        transport=transport,
    )
