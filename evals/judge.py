"""LLM judge for the three criteria rules cannot check (PLAN, "Reglas y juez").

One criterion per call (faithfulness, key_points, premise_corrected), temperature 0, strict
JSON {"verdict": "pass" | "fail" | "unknown", "reason": "..."}. The prompts are versioned
files in evals/prompts/; their version (file stem + sha256) goes into every run.json, and
a change of judge model or prompt means calibrating again (judge_sample/judge_agreement).

An answer that cannot be parsed is recorded as "unknown" with parse_ok=False: counted,
never dropped.

The judge uses app.llm's OpenAI-compatible client with the JUDGE_* settings in place of
the LLM_* ones.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.settings import Settings
from evals.schema import Case, ExpectedBehavior

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
CRITERIA = ("faithfulness", "key_points", "premise_corrected")
PROMPT_FILES = {criterion: f"judge_{criterion}_v1.md" for criterion in CRITERIA}
VERDICTS = ("pass", "fail", "unknown")

VERDICT_SCHEMA = {
    "name": "judge_verdict",
    "schema": {
        "type": "object",
        "properties": {
            # "unknown" first: a schema-filling fake answers with the first value.
            "verdict": {"type": "string", "enum": ["unknown", "pass", "fail"]},
            "reason": {"type": "string"},
        },
        "required": ["verdict", "reason"],
        "additionalProperties": False,
    },
}


class JudgeNotConfigured(RuntimeError):
    pass


# --- prompts ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^## (system|user)\s*$", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str  # file stem, e.g. judge_faithfulness_v1
    system: str
    user: str
    sha256: str

    @property
    def version(self) -> str:
        return f"{self.name}@{self.sha256[:8]}"

    def render(self, **values: str) -> list[dict[str, str]]:
        def fill(template: str) -> str:
            def sub(m: re.Match) -> str:
                if m.group(1) not in values:
                    raise KeyError(f"{self.name}: falta el valor {{{{{m.group(1)}}}}}")
                return values[m.group(1)]

            return _PLACEHOLDER_RE.sub(sub, template)

        return [
            {"role": "system", "content": fill(self.system)},
            {"role": "user", "content": fill(self.user)},
        ]


def load_prompt(path: Path) -> Prompt:
    """A Markdown file with a '## system' and a '## user' section (anything before the
    first section is a note for people and is not sent)."""
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    text = raw.decode("utf-8")
    parts = _SECTION_RE.split(text)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if set(sections) != {"system", "user"}:
        raise ValueError(f"{path}: necesita exactamente una sección '## system' y una '## user'")
    return Prompt(path.stem, sections["system"], sections["user"], hashlib.sha256(raw).hexdigest())


def load_judge_prompts(directory: Path = PROMPTS_DIR) -> dict[str, Prompt]:
    return {c: load_prompt(directory / name) for c, name in PROMPT_FILES.items()}


# --- verdicts --------------------------------------------------------------------------


@dataclass
class Verdict:
    criterion: str
    verdict: str  # pass | fail | unknown
    reason: str
    parse_ok: bool = True
    source: str = "judge"  # "judge", or "rule" when decided without calling the judge
    model: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0

    def to_json(self) -> dict:
        return dict(self.__dict__)


def parse_verdict(text: str) -> tuple[str, str, bool]:
    """(verdict, reason, parse_ok). Accepts a JSON object alone, inside ``` fences, or
    surrounded by text; anything else is ("unknown", ..., False)."""
    cleaned = (text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            verdict = str(data.get("verdict", "")).strip().lower()
            reason = data.get("reason")
            if verdict in VERDICTS and isinstance(reason, str):
                return verdict, reason.strip(), True
    return "unknown", f"respuesta del juez no interpretable: {cleaned[:200]!r}", False


def criteria_for(case: Case, *, answered: bool) -> list[str]:
    """Criteria to judge for one case: faithfulness whenever the system answered (an
    answer to an unanswerable question can still make unsupported claims), key points
    when the case expects an answer, premise correction for false-premise cases."""
    out = []
    if answered:
        out.append("faithfulness")
    if case.expected_behavior != ExpectedBehavior.abstain and case.key_points:
        out.append("key_points")
    if case.expected_behavior == ExpectedBehavior.correct_premise:
        out.append("premise_corrected")
    return out


def max_judge_calls(case: Case) -> int:
    """Upper bound of judge calls for one case, for the cost estimate."""
    return len(criteria_for(case, answered=True))


def format_context(passages: Sequence[tuple[int, str, str]]) -> str:
    """Numbered passages: (n, locator, text)."""
    if not passages:
        return "(sin fragmentos)"
    return "\n\n".join(f"[{n}] {locator}\n{text}" for n, locator, text in passages)


def format_key_points(points: Sequence[str]) -> str:
    lines = []
    for point in points:
        alternatives = [a.strip() for a in point.split("|") if a.strip()]
        lines.append("- " + " | ".join(alternatives))
    return "\n".join(lines) if lines else "(ninguno)"


@dataclass
class Judge:
    llm: Any  # anything with app.llm's complete(); usually a cost.CachingLLM
    model_id: str
    max_tokens: int
    prompts: dict[str, Prompt] = field(default_factory=load_judge_prompts)

    @property
    def prompt_versions(self) -> dict[str, str]:
        return {c: p.version for c, p in self.prompts.items()}

    def evaluate(
        self,
        criterion: str,
        *,
        question: str,
        answer: str,
        context: Sequence[tuple[int, str, str]] = (),
        key_points: Sequence[str] = (),
    ) -> Verdict:
        messages = self.prompts[criterion].render(
            question=question,
            answer=answer,
            context=format_context(context),
            key_points=format_key_points(key_points),
        )
        response = self.llm.complete(
            messages, json_schema=VERDICT_SCHEMA, max_tokens=self.max_tokens, temperature=0
        )
        verdict, reason, ok = parse_verdict(getattr(response, "text", ""))
        return Verdict(
            criterion=criterion,
            verdict=verdict,
            reason=reason,
            parse_ok=ok,
            model=getattr(response, "model", "") or self.model_id,
            provider=getattr(response, "provider", "") or "",
            tokens_in=getattr(response, "tokens_in", 0) or 0,
            tokens_out=getattr(response, "tokens_out", 0) or 0,
        )


def judge_settings(settings: Settings) -> Settings:
    """A copy of `settings` whose LLM_* values are the JUDGE_* ones, for app.llm.get_llm.
    The fallback provider is cleared: the judge model is pinned, never swapped."""
    update = {
        "llm_base_url": settings.judge_base_url,
        "llm_model": settings.judge_model,
        "llm_api_key": settings.judge_api_key,
        "llm_fallback_base_url": "",
        "llm_fallback_model": "",
        "llm_fallback_api_key": "",
    }
    known = type(settings).model_fields
    return settings.model_copy(update={k: v for k, v in update.items() if k in known})


def get_judge(settings: Settings, cache: Any) -> Judge:
    """Judge on app.llm with the JUDGE_* settings, wrapped in the eval cache."""
    from app.llm import get_llm  # imported here: evals without a judge do not need it
    from evals.cost import CachingLLM

    fake = getattr(settings, "llm_provider", "") == "fake"
    if not settings.judge_model and not fake:
        raise JudgeNotConfigured(
            "Falta JUDGE_MODEL (y JUDGE_BASE_URL, JUDGE_API_KEY). El juez es un modelo fijado "
            "por ID, de otra familia que el generador si hay uno gratis. Sin juez: --no-judge."
        )
    model_id = settings.judge_model or "fake-judge"
    llm = CachingLLM.from_settings(
        get_llm(judge_settings(settings)), model_id=model_id, settings=settings, cache=cache
    )
    return Judge(llm=llm, model_id=model_id, max_tokens=settings.eval_judge_max_tokens)
