"""Shared data contracts (PLAN, "Contratos de datos").

Every module reads and writes these types. Changing a field here is a contract change:
update the plan, the migrations and the tests together.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app import bekker


class Lang(StrEnum):
    en = "en"
    es = "es"
    grc = "grc"  # Greek: used only to derive Bekker ranges, never indexed


class Authenticity(StrEnum):
    genuine = "genuine"
    disputed = "disputed"
    spurious = "spurious"


class Structure(StrEnum):
    book_chapter = "book_chapter"
    chapter = "chapter"
    book_bekker_page = "book_bekker_page"
    book_problem = "book_problem"


class TextType(StrEnum):
    aristotle = "aristotle"  # the work itself: the only type that is indexed
    note = "note"  # translator footnote
    summary = "summary"  # translator/editor chapter summary
    introduction = "introduction"


class BekkerPrecision(StrEnum):
    line = "line"  # every boundary sits on a real line marker
    interpolated = "interpolated"  # lines interpolated by word count between markers
    page = "page"  # page+column known, lines not
    chapter = "chapter"  # only a chapter-level range (e.g. from a concordance table)
    none = "none"


class SourceStatus(StrEnum):
    indexed = "indexed"
    waiting = "waiting"  # "en espera": US public domain only, or licence to be confirmed
    excluded = "excluded"
    reference = "reference"  # used only to derive Bekker ranges (Greek text)


class Work(BaseModel):
    """One row of corpus/works.yaml (the catalogue)."""

    work_id: str
    abbr: str
    title_es: str
    title_en: str
    title_la: str
    aliases: list[str] = Field(default_factory=list)
    bekker_start: str | None = None  # e.g. "1094a"; None when the work is outside Bekker
    bekker_end: str | None = None
    authenticity: Authenticity
    structure: Structure
    perseus_id: str | None = None  # e.g. "tlg010"

    def bekker_range(self) -> tuple[int, int] | None:
        if not self.bekker_start or not self.bekker_end:
            return None
        s = bekker.parse(self.bekker_start)
        e = bekker.parse(self.bekker_end)
        start = s.to_int() if s.line else bekker.column_start(s.page, s.column)
        end = e.to_int() if e.line else bekker.column_end(e.page, e.column)
        return start, end


class Edition(BaseModel):
    """One row of corpus/sources.yaml (the manifest)."""

    edition_id: str
    work_id: str
    lang: Lang
    translator: str | None = None  # None for an original-language text
    translator_death_year: int | None = None
    pub_year: int | None = None
    url: str
    pinned_version: str  # commit SHA, tag or retrieval date
    sha256: str | None = None  # filled by `make corpus` on first download, then enforced
    licence: str
    licence_evidence: str
    attribution: str
    adapter: str
    tier: int
    has_bekker: bool
    status: SourceStatus
    status_reason: str = ""


class Unit(BaseModel):
    """What `make corpus` produces, before chunking: the smallest division the edition marks."""

    unit_id: str  # stable: "<edition_id>:<div path>", e.g. "eth-nic.en.rackham:2.6"
    work_id: str
    edition_id: str
    lang: Lang
    divisions: list[tuple[str, str]]  # ordered (level, label), e.g. [("book","2"),("chapter","6")]
    seq: int  # order of the unit inside its edition
    bekker_start: int | None = None
    bekker_end: int | None = None
    bekker_precision: BekkerPrecision = BekkerPrecision.none
    # Character offset -> encoded Bekker position for every marker inside `text`.
    # Lets chunking compute exact or interpolated ranges for sub-unit fragments.
    bekker_anchors: list[tuple[int, int]] = Field(default_factory=list)
    text_type: TextType = TextType.aristotle
    text: str
    notes: list[str] = Field(default_factory=list)  # translator footnotes, kept apart


class Chunk(BaseModel):
    """A fragment produced by chunking inside one unit; inherits its fields."""

    chunk_id: str  # stable: "<unit_id>#<n>"
    unit_id: str
    work_id: str
    edition_id: str
    lang: Lang
    divisions: list[tuple[str, str]]
    seq: int  # order inside the edition
    bekker_start: int | None = None
    bekker_end: int | None = None
    bekker_precision: BekkerPrecision = BekkerPrecision.none
    text: str  # original text, shown to people and quoted
    embed_text: str  # header + text: what gets embedded


class RetrievedChunk(BaseModel):
    """One retrieval hit. Returned by app.retrieval.retrieve()."""

    chunk: Chunk
    score: float  # higher is better; for dense search, cosine similarity
    rank: int  # 1-based
    method: str  # "dense", "bekker_ref", "hybrid", ...


class Citation(BaseModel):
    """A citation built by the backend from chunk metadata, never typed by the model."""

    n: int  # number the model used, [1]..[k]
    chunk_id: str
    work_id: str
    work_title: str
    edition_id: str
    translator: str | None
    lang: Lang
    locator: str  # human form: "EN II.6, 1106b36–1107a8 (aprox.)"
    bekker_start: int | None = None
    bekker_end: int | None = None
    authenticity: Authenticity
    licence: str
    source_url: str
    text: str


class AskResult(BaseModel):
    """What app.generation.answer() returns and POST /ask serialises."""

    answer: str
    answer_lang: str  # "es" or "en"
    abstained: bool
    # "low_similarity" | "model_unanswerable" | "no_valid_citations"
    abstain_reason: str | None = None
    search_only: bool = False  # True when the LLM failed or the budget ran out
    citations: list[Citation] = Field(default_factory=list)
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    index_version: str = ""
    prompt_version: str = ""
    model: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: dict[str, float] = Field(default_factory=dict)  # per stage: embed, retrieve, llm
    error: str | None = None
