"""Eval dataset schema and loader (PLAN, "Diseño de los evals").

One case per line in evals/dataset/*.jsonl. Expected evidence is a list of groups; each
group lists alternative locations (parallel passages) and retrieving any one of them hits
the group. A location is a work plus a Bekker range, or, for editions without Bekker, a
work plus the book and chapter of a named edition. Never a chunk id: labels must survive
re-chunking.

Runners ignore cases without `revisado_por` and say how many they ignored; the format
check (`make test`) validates every case, reviewed or not.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app import bekker

DATASET_DIR = Path(__file__).resolve().parent / "dataset"
SMOKE_FILE = Path(__file__).resolve().parent / "smoke.jsonl"


class CaseType(StrEnum):
    lookup = "lookup"
    definition = "definition"
    synthesis = "synthesis"
    cross_work = "cross_work"
    bekker_ref = "bekker_ref"
    unanswerable = "unanswerable"
    false_premise = "false_premise"
    dubious_work = "dubious_work"
    injection = "injection"


class UnanswerableReason(StrEnum):
    out_of_domain = "out_of_domain"
    lost_or_not_ingested = "lost_or_not_ingested"
    near_miss = "near_miss"  # topic present, the asked fact absent
    opinion = "opinion"


class ExpectedBehavior(StrEnum):
    answer = "answer"
    abstain = "abstain"
    correct_premise = "correct_premise"
    answer_with_caveat = "answer_with_caveat"  # cites a disputed or spurious work


class Split(StrEnum):
    dev = "dev"
    test = "test"


class CaseSource(StrEnum):
    handwritten = "handwritten"
    synthetic_reviewed = "synthetic_reviewed"
    synthetic_unreviewed = "synthetic_unreviewed"


# PLAN, "Diseño de los evals": "Las de comparación, sin respuesta, premisa falsa y obra
# dudosa las escribe la persona". A model may propose them, but a reviewed one must be
# handwritten; any exception is a recorded decision, not a default path.
PERSON_WRITTEN_TYPES = frozenset(
    {CaseType.cross_work, CaseType.unanswerable, CaseType.false_premise, CaseType.dubious_work}
)


class BekkerSpan(BaseModel):
    """A work plus a Bekker range, e.g. eth-nic 1106b36-1107a8. A ref without a line
    ("1106b") covers its whole column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    work_id: str
    bekker_start: str
    bekker_end: str

    @field_validator("bekker_start", "bekker_end")
    @classmethod
    def _parses(cls, value: str) -> str:
        bekker.parse(value)  # raises BekkerError (a ValueError) when malformed
        return value.strip()

    @model_validator(mode="after")
    def _ordered(self) -> BekkerSpan:
        start, end = self.encoded()
        if end < start:
            raise ValueError(f"bekker_end {self.bekker_end} antes de bekker_start")
        return self

    def encoded(self) -> tuple[int, int]:
        """Inclusive encoded range (app.bekker)."""
        s = bekker.parse(self.bekker_start)
        e = bekker.parse(self.bekker_end)
        start = s.to_int() if s.line is not None else bekker.column_start(s.page, s.column)
        end = e.to_int() if e.line is not None else bekker.column_end(e.page, e.column)
        return start, end

    def label(self) -> str:
        return f"{self.work_id} {bekker.format_range(*self.encoded())}"


class DivisionSpan(BaseModel):
    """A work plus book and chapter, for editions without Bekker.

    Chapter numbering differs between editions, so the division belongs to one edition:
    `edition_note` says which, for people; `edition_id`, when set, restricts the match to
    that edition's chunks. `book` is None for works divided only in chapters.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    work_id: str
    book: str | None = None
    chapter: str
    edition_note: str
    edition_id: str | None = None

    def label(self) -> str:
        book = f"{self.book}." if self.book else ""
        return f"{self.work_id} {book}{self.chapter} ({self.edition_note})"


Span = BekkerSpan | DivisionSpan


def _check_lang(value: str) -> str:
    if value not in ("es", "en"):
        raise ValueError("question_lang debe ser 'es' o 'en'")
    return value


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    question_lang: str
    type: CaseType
    unanswerable_reason: UnanswerableReason | None = None
    expected_behavior: ExpectedBehavior
    evidence: list[list[Span]] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    split: Split
    source: CaseSource
    revisado_por: str | None = None
    fecha_revision: date | None = None
    corpus_version_checked: str | None = None
    twin_of: str | None = None

    @field_validator("question_lang")
    @classmethod
    def _lang(cls, value: str) -> str:
        return _check_lang(value)

    @field_validator("evidence")
    @classmethod
    def _groups_not_empty(cls, groups: list[list[Span]]) -> list[list[Span]]:
        if any(not group for group in groups):
            raise ValueError("cada grupo de evidencia necesita al menos una ubicación")
        return groups

    @field_validator("key_points")
    @classmethod
    def _key_points_text(cls, points: list[str]) -> list[str]:
        for point in points:
            if not point.strip() or any(not alt.strip() for alt in point.split("|")):
                raise ValueError(f"dato clave vacío o alternativa vacía: {point!r}")
        return points

    @model_validator(mode="after")
    def _consistent(self) -> Case:
        if (self.type == CaseType.unanswerable) != (self.unanswerable_reason is not None):
            raise ValueError("unanswerable_reason va solo y siempre con type=unanswerable")
        if self.type == CaseType.unanswerable and self.expected_behavior != "abstain":
            raise ValueError("una pregunta unanswerable espera expected_behavior=abstain")
        if self.expected_behavior in ("answer", "answer_with_caveat") and not self.evidence:
            raise ValueError("una pregunta con respuesta necesita al menos un grupo de evidencia")
        n = len(self.key_points)
        if self.expected_behavior == "abstain":
            if n > 4:
                raise ValueError("entre 0 y 4 datos clave en una pregunta sin respuesta")
        elif not 2 <= n <= 4:
            raise ValueError(f"entre 2 y 4 datos clave; hay {n}")
        if self.revisado_por is not None and not self.revisado_por.strip():
            raise ValueError("revisado_por vacío: usar null si no está revisada")
        if self.revisado_por and self.fecha_revision is None:
            raise ValueError("una pregunta revisada necesita fecha_revision")
        if self.revisado_por and self.source == CaseSource.synthetic_unreviewed:
            raise ValueError("revisada pero con source=synthetic_unreviewed")
        if (
            self.revisado_por
            and self.type in PERSON_WRITTEN_TYPES
            and self.source != CaseSource.handwritten
        ):
            raise ValueError(
                f"las preguntas {self.type.value} las escribe la persona: una revisada tiene "
                "que tener source=handwritten (reescribirla o reemplazarla)"
            )
        if self.twin_of == self.id:
            raise ValueError("twin_of apunta a la misma pregunta")
        return self

    @property
    def reviewed(self) -> bool:
        return bool(self.revisado_por)

    @property
    def expects_answer(self) -> bool:
        return self.expected_behavior != ExpectedBehavior.abstain

    @property
    def scored_for_retrieval(self) -> bool:
        """Retrieval metrics cover cases that expect an answer and have evidence."""
        return self.expects_answer and bool(self.evidence)


class SmokeCase(BaseModel):
    """One line of evals/smoke.jsonl: a fixed question for `make smoke` (PLAN, Etapa 4)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    question_lang: str
    expected_behavior: ExpectedBehavior
    note: str = ""

    @field_validator("question_lang")
    @classmethod
    def _lang(cls, value: str) -> str:
        return _check_lang(value)


@dataclass
class LoadedDataset:
    cases: list[Case]  # every valid case, reviewed or not, in file order
    errors: list[str] = field(default_factory=list)  # "file:line: message"
    files: list[Path] = field(default_factory=list)
    version: str = ""

    @property
    def reviewed(self) -> list[Case]:
        return [c for c in self.cases if c.reviewed]

    @property
    def unreviewed(self) -> list[Case]:
        return [c for c in self.cases if not c.reviewed]


def dataset_files(directory: Path = DATASET_DIR) -> list[Path]:
    return sorted(directory.glob("*.jsonl"))


def file_set_version(files: list[Path]) -> str:
    """sha256 of the file names and contents, with line endings normalised so a Windows
    checkout (CRLF) gets the same version as CI."""
    h = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.name):
        h.update(path.name.encode("utf-8") + b"\0")
        h.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


def _format_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return "; ".join(parts)


def read_jsonl(path: Path, model: type[BaseModel]) -> tuple[list, list[str]]:
    """Parse one JSONL file into `model` objects; errors are 'name:line: message'."""
    items: list = []
    errors: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                items.append(model.model_validate(json.loads(line)))
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{lineno}: JSON inválido: {exc.msg}")
            except ValidationError as exc:
                errors.append(f"{path.name}:{lineno}: {_format_error(exc)}")
    return items, errors


def load_dataset(directory: Path = DATASET_DIR) -> LoadedDataset:
    files = dataset_files(directory)
    cases: list[Case] = []
    errors: list[str] = []
    for path in files:
        items, errs = read_jsonl(path, Case)
        cases.extend(items)
        errors.extend(errs)
    return LoadedDataset(cases=cases, errors=errors, files=files, version=file_set_version(files))


def load_smoke(path: Path = SMOKE_FILE) -> tuple[list[SmokeCase], list[str]]:
    if not path.exists():
        return [], [f"{path} no existe: faltan las 10 preguntas de `make smoke`"]
    return read_jsonl(path, SmokeCase)
