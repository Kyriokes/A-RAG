"""Rule-based eval metrics: no LLM, no database (PLAN, "Diseño de los evals").

A retrieved or cited fragment is reduced to a `Location` (ids, Bekker range, divisions;
never its text). A location hits a gold span when:

- Bekker span: same work, and the fragment's range overlaps the gold range widened by
  `tolerance` lines on each side by at least `min_lines` lines (capped at the length of
  the shorter of the two, so a fragment entirely inside the passage always hits).
- Division span (editions without Bekker): same work, same book and chapter, and the
  same edition when the span names one.

Lines are counted with fixed-length columns of settings.bekker_column_lines lines (the
length chunking assumes when it interpolates across a column break), so overlaps and the
tolerance work the same way across column and page breaks as inside a column. A line past
that length is clamped to the column's last line, and column-only references (line 0 /
line 99 in the encoding) to the first / last line of the column.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app import bekker
from app.settings import Settings, get_settings
from evals.schema import BekkerSpan, Case, DivisionSpan, ExpectedBehavior, Span


@dataclass(frozen=True)
class MatchParams:
    tolerance_lines: int
    min_overlap_lines: int
    column_lines: int = Settings.model_fields["bekker_column_lines"].default

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> MatchParams:
        s = settings or get_settings()
        return cls(s.eval_overlap_tolerance_lines, s.eval_overlap_min_lines, s.bekker_column_lines)


@dataclass(frozen=True)
class Location:
    """Where a fragment sits. Stored in per_question.jsonl instead of any corpus text."""

    chunk_id: str
    work_id: str
    edition_id: str
    lang: str
    bekker_start: int | None = None
    bekker_end: int | None = None
    divisions: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_chunk(cls, chunk: Any) -> Location:
        return cls(
            chunk_id=chunk.chunk_id,
            work_id=chunk.work_id,
            edition_id=chunk.edition_id,
            lang=str(getattr(chunk.lang, "value", chunk.lang)),
            bekker_start=chunk.bekker_start,
            bekker_end=chunk.bekker_end,
            divisions=tuple((str(a), str(b)) for a, b in chunk.divisions),
        )

    @classmethod
    def from_json(cls, data: dict) -> Location:
        return cls(
            chunk_id=data["chunk_id"],
            work_id=data["work_id"],
            edition_id=data["edition_id"],
            lang=data["lang"],
            bekker_start=data.get("bekker_start"),
            bekker_end=data.get("bekker_end"),
            divisions=tuple(tuple(d) for d in data.get("divisions", [])),
        )

    def to_json(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "work_id": self.work_id,
            "edition_id": self.edition_id,
            "lang": self.lang,
            "bekker_start": self.bekker_start,
            "bekker_end": self.bekker_end,
            "bekker": bekker.format_range(self.bekker_start, self.bekker_end),
            "divisions": [list(d) for d in self.divisions],
        }


# --- span overlap ----------------------------------------------------------------------


def line_position(value: int, column_lines: int) -> int:
    """Running line number of an encoded Bekker ref, with `column_lines` lines per column
    (see the module docstring for the clamping)."""
    ref = bekker.decode(value)
    column = ref.page * 2 + (1 if ref.column == "b" else 0)
    line = min(max(ref.line or 0, 1), column_lines)
    return column * column_lines + line


def line_interval(start: int, end: int, column_lines: int) -> tuple[int, int]:
    return line_position(start, column_lines), line_position(end, column_lines)


def bekker_overlap_hit(
    fragment: tuple[int, int], gold: tuple[int, int], params: MatchParams
) -> bool:
    """Both ranges are encoded and inclusive; see the module docstring."""
    fs, fe = line_interval(*fragment, params.column_lines)
    gs, ge = line_interval(*gold, params.column_lines)
    gs -= params.tolerance_lines
    ge += params.tolerance_lines
    shared = min(fe, ge) - max(fs, gs) + 1
    if shared <= 0:
        return False
    needed = min(params.min_overlap_lines, fe - fs + 1, ge - gs + 1)
    return shared >= max(needed, 1)


def division_hit(loc: Location, span: DivisionSpan) -> bool:
    if loc.work_id != span.work_id:
        return False
    if span.edition_id and loc.edition_id != span.edition_id:
        return False
    divisions = dict(loc.divisions)
    if divisions.get("chapter") != span.chapter:
        return False
    return span.book is None or divisions.get("book") == span.book


def span_hit(loc: Location, span: Span, params: MatchParams) -> bool:
    if isinstance(span, DivisionSpan):
        return division_hit(loc, span)
    if loc.work_id != span.work_id or loc.bekker_start is None or loc.bekker_end is None:
        return False
    return bekker_overlap_hit((loc.bekker_start, loc.bekker_end), span.encoded(), params)


def spans_overlap(a: Span, b: Span) -> bool:
    """Do two gold spans name the same passage (any shared line, no tolerance)?"""
    if a.work_id != b.work_id:
        return False
    if isinstance(a, BekkerSpan) and isinstance(b, BekkerSpan):
        # The encoding preserves order, so encoded ranges share a value iff they share a line.
        (a0, a1), (b0, b1) = a.encoded(), b.encoded()
        return min(a1, b1) >= max(a0, b0)
    if isinstance(a, DivisionSpan) and isinstance(b, DivisionSpan):
        same_edition = a.edition_id is None or b.edition_id is None or a.edition_id == b.edition_id
        return same_edition and a.book == b.book and a.chapter == b.chapter
    return False


def group_hit(loc: Location, group: Sequence[Span], params: MatchParams) -> bool:
    return any(span_hit(loc, span, params) for span in group)


# --- retrieval metrics -----------------------------------------------------------------


def groups_hit_by(
    locations: Sequence[Location], groups: Sequence[Sequence[Span]], params: MatchParams
) -> list[list[int]]:
    """For each location (in rank order), the indices of the gold groups it hits."""
    return [
        [g for g, group in enumerate(groups) if group_hit(loc, group, params)] for loc in locations
    ]


def ranks_from_hits(hit_groups: Sequence[Sequence[int]], n_groups: int) -> list[int | None]:
    """1-based rank of the first location hitting each group (from groups_hit_by)."""
    ranks: list[int | None] = [None] * n_groups
    for rank, groups in enumerate(hit_groups, start=1):
        for g in groups:
            if ranks[g] is None:
                ranks[g] = rank
    return ranks


def first_hit_ranks(
    locations: Sequence[Location], groups: Sequence[Sequence[Span]], params: MatchParams
) -> list[int | None]:
    """1-based rank of the first location hitting each group; None when none does."""
    return ranks_from_hits(groups_hit_by(locations, groups, params), len(groups))


def recall_at(first_ranks: Sequence[int | None], k: int) -> float | None:
    """Evidence recall@k: share of groups hit within the first k. None without groups."""
    if not first_ranks:
        return None
    return sum(1 for r in first_ranks if r is not None and r <= k) / len(first_ranks)


def all_evidence_at(first_ranks: Sequence[int | None], k: int) -> bool | None:
    if not first_ranks:
        return None
    return all(r is not None and r <= k for r in first_ranks)


def reciprocal_rank(first_ranks: Sequence[int | None], cutoff: int = 10) -> float | None:
    """MRR@cutoff term, defined only for single-group cases (None otherwise)."""
    if len(first_ranks) != 1:
        return None
    r = first_ranks[0]
    return 1.0 / r if r is not None and r <= cutoff else 0.0


# --- abstention ------------------------------------------------------------------------


@dataclass
class AbstentionMatrix:
    """Rows: what the case expects. Columns: what the system did."""

    abstain_answered: int = 0  # false answers
    abstain_abstained: int = 0
    answer_answered: int = 0
    answer_abstained: int = 0  # false refusals

    def add(self, expects_answer: bool, abstained: bool) -> None:
        if expects_answer:
            if abstained:
                self.answer_abstained += 1
            else:
                self.answer_answered += 1
        elif abstained:
            self.abstain_abstained += 1
        else:
            self.abstain_answered += 1

    @property
    def n_abstain(self) -> int:
        return self.abstain_answered + self.abstain_abstained

    @property
    def n_answer(self) -> int:
        return self.answer_answered + self.answer_abstained

    @property
    def false_answer_rate(self) -> float | None:
        return self.abstain_answered / self.n_abstain if self.n_abstain else None

    @property
    def false_refusal_rate(self) -> float | None:
        return self.answer_abstained / self.n_answer if self.n_answer else None

    def to_json(self) -> dict:
        return {
            "abstain_answered": self.abstain_answered,
            "abstain_abstained": self.abstain_abstained,
            "answer_answered": self.answer_answered,
            "answer_abstained": self.answer_abstained,
            "false_answer_rate": self.false_answer_rate,
            "false_refusal_rate": self.false_refusal_rate,
        }


def abstention_matrix(pairs: Iterable[tuple[bool, bool]]) -> AbstentionMatrix:
    """`pairs` of (case expects an answer, system abstained)."""
    matrix = AbstentionMatrix()
    for expects_answer, abstained in pairs:
        matrix.add(expects_answer, abstained)
    return matrix


# --- citations -------------------------------------------------------------------------


@dataclass
class CitationCheck:
    n_citations: int
    valid: int  # well-formed, and consistent with the chunk it names when that is in context
    in_context: int  # names a chunk that was passed to the model
    hits_gold: int  # hits some gold evidence group
    groups_cited: int  # gold groups hit by at least one citation
    n_groups: int
    # Per citation, in order: {"valid", "in_context", "groups"} (gold groups it hits).
    details: list[dict] = field(default_factory=list)

    @property
    def all_valid(self) -> bool:
        return self.valid == self.n_citations

    @property
    def all_in_context(self) -> bool:
        return self.in_context == self.n_citations

    @property
    def precision(self) -> float | None:
        if not self.n_citations or not self.n_groups:
            return None
        return self.hits_gold / self.n_citations

    @property
    def recall(self) -> float | None:
        return self.groups_cited / self.n_groups if self.n_groups else None

    def to_json(self) -> dict:
        return {
            "n_citations": self.n_citations,
            "valid": self.valid,
            "in_context": self.in_context,
            "hits_gold": self.hits_gold,
            "groups_cited": self.groups_cited,
            "n_groups": self.n_groups,
            "precision": self.precision,
            "recall": self.recall,
        }


def citation_location(citation: Any) -> Location:
    """A Citation (app.models) reduced to its location."""
    return Location(
        chunk_id=citation.chunk_id,
        work_id=citation.work_id,
        edition_id=citation.edition_id,
        lang=str(getattr(citation.lang, "value", citation.lang)),
        bekker_start=citation.bekker_start,
        bekker_end=citation.bekker_end,
    )


def check_citations(
    citations: Sequence[Any],
    context: Sequence[Location],
    groups: Sequence[Sequence[Span]],
    params: MatchParams,
) -> CitationCheck:
    by_id = {loc.chunk_id: loc for loc in context}
    valid = in_context = hits = 0
    cited_groups: set[int] = set()
    details: list[dict] = []
    for citation in citations:
        loc = citation_location(citation)
        ctx = by_id.get(loc.chunk_id)
        well_formed = citation.n >= 1 and bool(citation.locator) and bool(loc.chunk_id)
        consistent = ctx is None or (
            (ctx.work_id, ctx.edition_id, ctx.bekker_start, ctx.bekker_end)
            == (loc.work_id, loc.edition_id, loc.bekker_start, loc.bekker_end)
        )
        valid += int(well_formed and consistent)
        in_context += int(ctx is not None)
        # Score against gold with the context chunk's divisions when known.
        target = ctx if ctx is not None else loc
        hit_groups = [g for g, group in enumerate(groups) if group_hit(target, group, params)]
        hits += int(bool(hit_groups))
        cited_groups.update(hit_groups)
        details.append(
            {
                "valid": well_formed and consistent,
                "in_context": ctx is not None,
                "groups": hit_groups,
            }
        )
    return CitationCheck(
        n_citations=len(citations),
        valid=valid,
        in_context=in_context,
        hits_gold=hits,
        groups_cited=len(cited_groups),
        n_groups=len(groups),
        details=details,
    )


# --- answers ---------------------------------------------------------------------------


def fold(text: str) -> str:
    """Case- and accent-insensitive form for lexical checks."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).split())


def key_point_present(answer: str, key_point: str) -> bool:
    """Lexical check: any alternative of "a|b" appears in the answer."""
    folded = fold(answer)
    return any(fold(alt) in folded for alt in key_point.split("|") if alt.strip())


def key_points_lexical(answer: str, key_points: Sequence[str]) -> float | None:
    """Share of key points present verbatim (diagnostic; the judge checks concepts)."""
    if not key_points:
        return None
    return sum(key_point_present(answer, p) for p in key_points) / len(key_points)


def behavior_met(case: Case, *, abstained: bool, cites_dubious: bool) -> bool:
    """Expected behaviour by rules. Premise correction itself is left to the judge."""
    if case.expected_behavior == ExpectedBehavior.abstain:
        return abstained
    if case.expected_behavior == ExpectedBehavior.answer_with_caveat:
        return not abstained and cites_dubious
    return not abstained


def strict_pass(
    case: Case,
    *,
    abstained: bool,
    cites_dubious: bool,
    citations: CitationCheck | None,
    verdicts: dict[str, str] | None,
) -> bool | None:
    """Strict end-to-end pass (PLAN, métrica 2): expected behaviour, key points, at least
    one expected passage cited, no citation outside the context, and a faithful answer.

    `verdicts` maps judge criteria to "pass" / "fail" / "unknown"; None means no judge
    ran, and then the strict pass is not computed for any case (None): reporting only the
    cases the rules can settle (abstentions and rule failures) would average a biased
    subset. "unknown" counts as not passing.
    """
    if verdicts is None:
        return None
    if not behavior_met(case, abstained=abstained, cites_dubious=cites_dubious):
        return False
    if case.expected_behavior == ExpectedBehavior.abstain:
        return True
    if citations is None or citations.n_citations == 0 or not citations.all_in_context:
        return False
    if case.evidence and citations.groups_cited == 0:
        return False
    if verdicts is None:
        return None
    needed = ["faithfulness", "key_points"]
    if case.expected_behavior == ExpectedBehavior.correct_premise:
        needed.append("premise_corrected")
    return all(verdicts.get(c) == "pass" for c in needed)
