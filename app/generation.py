"""Answer generation with citations by construction (PLAN, Etapa 4).

answer() retrieves k passages, numbers them and asks the LLM for JSON
{"answerable", "answer", "citations"}. The model only points at passage numbers; the
backend turns each valid number into a citation from chunk metadata (rule 2).

It abstains, with the fixed message in the question's language, at three points:

1. low_similarity: retrieval found nothing, or settings.abstain_min_similarity is set and
   the best dense score is below it. The LLM is not called. Never applied when retrieval
   resolved an explicit Bekker reference (method "bekker_ref").
2. model_unanswerable: the model says the passages do not answer the question.
3. no_valid_citations: the model answered but cited no passage in 1..n. Numbers outside
   that range are dropped (and logged) before this check, and also removed from the text.

An LLM outage, a configuration error, or JSON that is still invalid after one retry gives
a search-only result (search_only=True, error set, abstained=False): the passages with
their citations and no generated answer. So does `skip_llm` (the API passes it when the
daily token budget is spent), without calling the LLM. The context sent to the model is
capped at settings.context_max_tokens by dropping passages from the last one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app import retrieval
from app.chunking import estimate_tokens
from app.citations import Catalogue, build_citation, load_catalogue
from app.llm import LLMError, LLMUnavailable, get_llm
from app.models import AskResult, Authenticity, Citation, RetrievedChunk
from app.query_parse import detect_lang
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "answer_v1.md"

ANSWER_SCHEMA = {
    "name": "aristotle_answer",
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

ABSTENTION_MESSAGE = {
    "es": "No encuentro la respuesta a esta pregunta en los textos de Aristóteles disponibles.",
    "en": "I cannot find the answer to this question in the available texts of Aristotle.",
}
SEARCH_ONLY_MESSAGE = {
    "es": (
        "No se pudo generar una respuesta en este momento. "
        "Estos son los pasajes más relevantes que encontré."
    ),
    "en": "An answer could not be generated right now. These are the most relevant passages.",
}
AUTHENTICITY_CAVEAT = {
    "es": "Atención: esta respuesta cita obras cuya atribución a Aristóteles se discute: {works}.",
    "en": "Note: this answer cites works whose attribution to Aristotle is disputed: {works}.",
}
_AUTHENTICITY_WORDS = {
    "es": {"genuine": "auténtica", "disputed": "dudosa", "spurious": "espuria"},
    "en": {"genuine": "genuine", "disputed": "disputed", "spurious": "spurious"},
}
_LANG_WORDS = {"es": "castellano", "en": "inglés", "grc": "griego"}
_ANSWER_LANG_LINE = {
    "es": "Idioma de la respuesta: castellano.",
    "en": "Idioma de la respuesta: inglés (answer in English).",
}
_RETRY_NOTE = (
    "Tu respuesta anterior no era un objeto JSON válido con los campos answerable, answer y "
    "citations. Responde de nuevo solo con ese objeto JSON."
)

# Passage and question go inside <pasaje>/<pregunta> tags; the same tags inside the data
# are defused so a passage or question cannot close its own block (prompt injection).
_TAG_RE = re.compile(r"<(/?)(pasaje|pregunta)", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_MARKER_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([.,;:])")

Retriever = Callable[..., list[RetrievedChunk]]


@lru_cache(maxsize=8)
def load_prompt(path: Path = PROMPT_PATH) -> tuple[str, str]:
    """(system prompt, prompt_version). The version is the first 8 hex chars of the sha256
    of the file; the leading HTML comment is documentation and is not sent."""
    raw = path.read_bytes()
    version = hashlib.sha256(raw).hexdigest()[:8]
    text = re.sub(r"^\s*<!--.*?-->\s*", "", raw.decode("utf-8"), count=1, flags=re.DOTALL)
    return text.strip(), version


def _lang(code: str) -> str:
    return "en" if code == "en" else "es"


def _defuse(text: str) -> str:
    return _TAG_RE.sub(r"‹\1\2", text)


def render_passage(citation: Citation) -> str:
    authenticity = _AUTHENTICITY_WORDS["es"][citation.authenticity.value]
    return (
        f'<pasaje n="{citation.n}" obra="{citation.work_title}" '
        f'idioma="{_LANG_WORDS[citation.lang.value]}" autenticidad="{authenticity}">\n'
        f"{_defuse(citation.text)}\n</pasaje>"
    )


def fit_context(passages: list[Citation], settings: Settings) -> list[Citation]:
    """Drop passages from the last one until the rendered passages fit
    settings.context_max_tokens. The first passage is always kept."""
    kept = list(passages)
    sizes = [estimate_tokens(render_passage(p), settings) for p in kept]
    while len(kept) > 1 and sum(sizes) > settings.context_max_tokens:
        dropped = kept.pop()
        sizes.pop()
        log.info("Contexto: se descarta el pasaje %d (%s)", dropped.n, dropped.chunk_id)
    return kept


def build_messages(question: str, passages: list[Citation], answer_lang: str) -> list[dict]:
    system, _ = load_prompt()
    body = "\n\n".join(render_passage(p) for p in passages)
    user = (
        f"{_ANSWER_LANG_LINE[_lang(answer_lang)]}\n\n"
        f"Pasajes:\n\n{body}\n\n"
        f"<pregunta>\n{_defuse(question.strip())}\n</pregunta>"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


@dataclass
class ModelAnswer:
    answerable: bool
    answer: str
    citations: list[int]


def _validate(data: object) -> ModelAnswer | None:
    if not isinstance(data, dict):
        return None
    answerable, text, cites = data.get("answerable"), data.get("answer"), data.get("citations")
    if cites is None:
        cites = []
    if not isinstance(answerable, bool) or not isinstance(text, str) or not isinstance(cites, list):
        return None
    if answerable and not text.strip():
        return None
    numbers: list[int] = []
    for item in cites:
        if isinstance(item, int) and not isinstance(item, bool):
            numbers.append(item)
        elif isinstance(item, str) and item.strip().strip("[]").isdigit():
            numbers.append(int(item.strip().strip("[]")))
        else:
            return None
    return ModelAnswer(answerable, text.strip(), numbers)


def parse_model_output(text: str) -> ModelAnswer | None:
    """The model's JSON, tolerating a code fence or text around the object; None if invalid."""
    text = (text or "").strip()
    attempts = [text]
    fence = _FENCE_RE.search(text)
    if fence:
        attempts.append(fence.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        attempts.append(text[start : end + 1])
    for attempt in attempts:
        try:
            parsed = _validate(json.loads(attempt))
        except ValueError:
            continue
        if parsed is not None:
            return parsed
    return None


def clean_markers(text: str, valid: set[int]) -> tuple[str, list[int], list[int]]:
    """Remove passage markers outside `valid` from the answer text.

    Returns (text, valid numbers found in markers, invalid numbers removed).
    """
    found: list[int] = []
    dropped: list[int] = []

    def replace(match: re.Match) -> str:
        numbers = [int(x) for x in re.findall(r"\d+", match.group(1))]
        keep = [n for n in numbers if n in valid]
        dropped.extend(n for n in numbers if n not in valid)
        found.extend(keep)
        return f"[{', '.join(map(str, keep))}]" if keep else ""

    out = _MARKER_RE.sub(replace, text)
    if dropped:
        out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", re.sub(r"[ \t]{2,}", " ", out))
    return out.strip(), found, dropped


def authenticity_caveat(citations: list[Citation], answer_lang: str) -> str:
    """Fixed caveat naming the disputed or spurious works cited; "" when there are none."""
    lang = _lang(answer_lang)
    named: list[str] = []
    for c in citations:
        if c.authenticity == Authenticity.genuine:
            continue
        label = f"{c.work_title} ({_AUTHENTICITY_WORDS[lang][c.authenticity.value]})"
        if label not in named:
            named.append(label)
    return AUTHENTICITY_CAVEAT[lang].format(works=", ".join(named)) if named else ""


def similarity_gate(hits: list[RetrievedChunk]) -> tuple[str, float | None]:
    """What the low-similarity gate sees in the retrieved hits: ("always_abstain", None)
    when nothing was retrieved; ("always_pass", None) when an explicit Bekker reference
    resolved or no hit is dense; otherwise ("score", best dense score). The eval runner
    records this to calibrate settings.abstain_min_similarity."""
    if not hits:
        return "always_abstain", None
    dense = [h.score for h in hits if h.method == "dense"]
    if not dense or any(h.method == "bekker_ref" for h in hits):
        return "always_pass", None
    return "score", max(dense)


def _low_similarity(hits: list[RetrievedChunk], threshold: float | None) -> bool:
    gate, score = similarity_gate(hits)
    if gate == "always_abstain":
        return True
    if gate == "always_pass" or threshold is None:
        return False
    return score < threshold


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)


def _ask_llm(
    llm, messages: list[dict], settings: Settings, result: AskResult
) -> tuple[ModelAnswer | None, str | None]:
    """Call the LLM, retrying once on invalid JSON. Records model, provider and tokens of
    every call in `result`. Returns (parsed answer or None, error or None)."""
    try:
        llm = llm or get_llm(settings)
        result.model = getattr(llm, "model", "") or ""
        result.provider = getattr(llm, "provider", "") or ""
        for attempt in range(2):
            extra = [] if attempt == 0 else [{"role": "user", "content": _RETRY_NOTE}]
            response = llm.complete(
                [*messages, *extra],
                json_schema=ANSWER_SCHEMA,
                max_tokens=settings.output_max_tokens,
                temperature=0,
            )
            result.model = response.model or result.model
            result.provider = response.provider or result.provider
            result.tokens_in += response.tokens_in or 0
            result.tokens_out += response.tokens_out or 0
            parsed = parse_model_output(response.text)
            if parsed is not None:
                return parsed, None
            log.warning("JSON inválido del LLM (intento %d): %r", attempt + 1, response.text[:300])
    except LLMUnavailable as exc:
        log.warning("LLM no disponible: %s", exc)
        return None, f"llm_unavailable: {exc}"
    except LLMError as exc:
        log.error("Error del LLM: %s", exc)
        return None, f"llm_error: {exc}"
    return None, None


def answer(
    question: str,
    *,
    works: list[str] | None = None,
    lang: str | None = None,
    include_dubious: bool = True,
    retriever: Retriever | None = None,
    llm=None,
    conn=None,
    settings: Settings | None = None,
    catalogue: Catalogue | None = None,
    index_version: str | None = None,
    skip_llm: str | None = None,
) -> AskResult:
    """Answer `question` from the corpus. `works`, `lang` and `include_dubious` are
    retrieval filters; the answer is always in the question's language.

    With the default retriever, index_version is read from the database; pass it when
    injecting a retriever. `skip_llm` is an error ("code: detail") that makes the result
    search-only without calling the LLM; abstention for low similarity still applies.
    """
    s = settings or get_settings()
    started = time.perf_counter()
    answer_lang = detect_lang(question)
    catalogue = catalogue or load_catalogue()
    _, prompt_version = load_prompt()

    t = time.perf_counter()
    hits = (retriever or retrieval.retrieve)(
        question,
        k=s.retrieval_k,
        works=works,
        lang=lang,
        include_dubious=include_dubious,
        conn=conn,
    )
    timings = {"retrieve": _ms(t)}
    if index_version is None:
        index_version = retrieval.current_index_version(conn) if retriever is None else ""

    result = AskResult(
        answer="",
        answer_lang=answer_lang,
        abstained=False,
        retrieved=hits,
        index_version=index_version,
        prompt_version=prompt_version,
        latency_ms=timings,
    )

    def done() -> AskResult:
        result.latency_ms = {**timings, "total": _ms(started)}
        return result

    def abstain(reason: str) -> AskResult:
        result.answer = ABSTENTION_MESSAGE[_lang(answer_lang)]
        result.abstained = True
        result.abstain_reason = reason
        return done()

    if _low_similarity(hits, s.abstain_min_similarity):
        return abstain("low_similarity")

    candidates = [
        build_citation(i, hit.chunk, catalogue.works, catalogue.editions, lang=answer_lang)
        for i, hit in enumerate(hits, start=1)
    ]

    def search_only(error: str) -> AskResult:
        result.answer = SEARCH_ONLY_MESSAGE[_lang(answer_lang)]
        result.search_only = True
        result.citations = candidates
        result.error = error
        return done()

    if skip_llm:
        return search_only(skip_llm)

    passages = fit_context(candidates, s)
    messages = build_messages(question, passages, answer_lang)

    t = time.perf_counter()
    parsed, error = _ask_llm(llm, messages, s, result)
    timings["llm"] = _ms(t)
    if error is not None:
        return search_only(error)
    if parsed is None:
        return search_only("invalid_json: el modelo no devolvió JSON válido tras un reintento")
    if not parsed.answerable:
        return abstain("model_unanswerable")

    valid = set(range(1, len(passages) + 1))
    text, in_text, dropped = clean_markers(parsed.answer, valid)
    dropped = [n for n in parsed.citations if n not in valid] + dropped
    if dropped:
        log.warning("Citas fuera de 1..%d descartadas: %s", len(passages), dropped)
    numbers = sorted({n for n in parsed.citations if n in valid} | set(in_text))
    if not numbers:
        return abstain("no_valid_citations")

    result.citations = [passages[n - 1] for n in numbers]
    caveat = authenticity_caveat(result.citations, answer_lang)
    result.answer = f"{text}\n\n{caveat}" if caveat else text
    return done()
