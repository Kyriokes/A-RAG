"""Citations built by the backend from chunk metadata, never typed by the model (rule 2).

A citation names the work (title in the answer language), the location, the translator,
the licence, the source and the authenticity of the work. The location is

- with Bekker: abbreviation, book and chapter of the edition, and the Bekker range, e.g.
  "EN II.6, 1106b36–1107a8 (aprox.)". Lines are marked approximate unless every boundary
  sits on a real line marker (precision "line"); with only page and column known, the
  range is given by column ("Pol. I, 1253a–1253b").
- without Bekker: abbreviation, book and chapter, plus whose edition that division belongs
  to (PLAN, "Contratos de datos": ubicación canónica).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app import bekker
from app.models import BekkerPrecision, Chunk, Citation, Edition, Work

# Division levels left out of the locator when there is a Bekker range: a Bekker page is
# already in the range.
_SKIP_WITH_BEKKER = {"bekker_page"}


class CitationError(LookupError):
    """A chunk whose work or edition is not in the catalogue: the index and the catalogue
    disagree, and a citation without translator and licence must not be published."""


@dataclass(frozen=True)
class Catalogue:
    works: dict[str, Work]
    editions: dict[str, Edition]


@lru_cache(maxsize=1)
def load_catalogue() -> Catalogue:
    """corpus/works.yaml and corpus/sources.yaml, read once per process."""
    from app.corpus.catalog import load_sources, load_works

    return Catalogue(load_works(), {e.edition_id: e for e in load_sources()})


def roman(n: int) -> str:
    numerals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]  # fmt: skip
    out = ""
    for value, symbol in numerals:
        while n >= value:
            out += symbol
            n -= value
    return out


def work_title(work: Work, lang: str) -> str:
    return work.title_en if lang == "en" else work.title_es


def _divisions(chunk: Chunk, has_bekker: bool) -> str:
    """ "II.6" for book 2 chapter 6, "I" for a book cited by Bekker page, "6" for a chapter."""
    labels = []
    for level, label in chunk.divisions:
        if has_bekker and level in _SKIP_WITH_BEKKER:
            continue
        if level == "book" and label.isdigit():
            label = roman(int(label))
        labels.append(label)
    return ".".join(labels)


def _column(value: int) -> str:
    ref = bekker.decode(value)
    return f"{ref.page}{ref.column}"


def format_bekker(chunk: Chunk, lang: str = "es") -> str:
    """The Bekker part of a locator, or "" when the chunk has none."""
    start, end = chunk.bekker_start, chunk.bekker_end
    if start is None or chunk.bekker_precision == BekkerPrecision.none:
        return ""
    end = end if end is not None else start
    # Column-level bounds (line 0 = column start, MAX_LINE = column end) carry no line.
    column_bounds = bekker.decode(start).line is None or bekker.decode(end).line in (
        None,
        bekker.MAX_LINE,
    )
    if chunk.bekker_precision == BekkerPrecision.page or column_bounds:
        first, last = _column(start), _column(end)
        return first if first == last else f"{first}–{last}"
    approximate = chunk.bekker_precision != BekkerPrecision.line
    text = bekker.format_range(start, end, approximate=approximate)
    return text.replace("(aprox.)", "(approx.)") if lang == "en" else text


def format_locator(chunk: Chunk, work: Work, edition: Edition, lang: str = "es") -> str:
    """Human-readable location, e.g. "EN II.6, 1106b36–1107a8 (aprox.)"."""
    bekker_part = format_bekker(chunk, lang)
    divisions = _divisions(chunk, has_bekker=bool(bekker_part))
    head = f"{work.abbr or work_title(work, lang)} {divisions}".strip()
    if bekker_part:
        return f"{head}, {bekker_part}"
    if divisions and edition.translator:
        owner = (
            f"división de la edición de {edition.translator}"
            if lang != "en"
            else f"division of {edition.translator}'s edition"
        )
        return f"{head} ({owner})"
    return head


def build_citation(
    n: int,
    chunk: Chunk,
    works: dict[str, Work],
    editions: dict[str, Edition],
    *,
    lang: str = "es",
) -> Citation:
    """Citation number `n` for `chunk`; `lang` is the answer language ("es" or "en")."""
    work = works.get(chunk.work_id)
    edition = editions.get(chunk.edition_id)
    if work is None or edition is None:
        missing = chunk.work_id if work is None else chunk.edition_id
        raise CitationError(f"{chunk.chunk_id}: {missing} no está en el catálogo")
    return Citation(
        n=n,
        chunk_id=chunk.chunk_id,
        work_id=work.work_id,
        work_title=work_title(work, lang),
        edition_id=edition.edition_id,
        translator=edition.translator,
        lang=chunk.lang,
        locator=format_locator(chunk, work, edition, lang),
        bekker_start=chunk.bekker_start,
        bekker_end=chunk.bekker_end,
        authenticity=work.authenticity,
        licence=edition.licence,
        source_url=edition.url,
        text=chunk.text,
    )
