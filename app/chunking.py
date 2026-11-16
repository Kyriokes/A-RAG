"""Structure-guided chunking (PLAN, Etapa 2).

A unit (normally a chapter) is cut into chunks of about chunk_min_words..chunk_max_words
words. Cuts fall on paragraph boundaries; a paragraph that alone is too long is cut at
sentence boundaries (then at ";"/":", then between words as a last resort). When whole
paragraphs are too coarse to keep every chunk at chunk_min_words or more, the unit is
packed again from sentences. A chunk never crosses its unit, so it never crosses a
chapter, book or work.

Each chunk gets its own Bekker range, computed from the unit's anchors (character offset ->
encoded Bekker position) by interpolating on word counts between neighbouring anchors, and
clamped to the unit range. The text that gets embedded carries a fixed header, e.g.
"Aristóteles, Ética a Nicómaco, libro II, cap. 6, 1106b36–1107a8".

Token counts are estimated with settings.chars_per_token. Packing keeps every chunk,
header included, under settings.chunk_max_tokens, and a chunk that still exceeds it is a
hard error: Ollama truncates long inputs silently and Workers AI rejects them.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app import bekker
from app.models import BekkerPrecision, Chunk, Lang, TextType, Unit, Work
from app.settings import Settings, get_settings

# Bump when a code change alters the chunks produced for the same settings; it is part of
# the chunking config and therefore of index_version.
# 2: a chunk that stops before a marker ends on the line before it; sentence repacking.
CHUNKER_VERSION = 2

_WORD_RE = re.compile(r"\S+")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n\s*")
_NEWLINE_RE = re.compile(r"\n\s*")
# Fallback cut points inside a paragraph that is too long, tried in this order.
_SPLIT_PATTERNS = [
    re.compile(r"(?<=[.!?])[\"'”’»)\]]*\s+"),  # sentence end
    re.compile(r"(?<=[;:])\s+"),  # clause end
]

_AUTHOR = {"es": "Aristóteles", "en": "Aristotle"}
# Division levels left out of the header: a Bekker page is already in the Bekker range.
_HEADER_SKIP_LEVELS = {"bekker_page"}
_LEVEL_WORDS = {
    "es": {"book": "libro", "chapter": "cap.", "section": "sección", "problem": "problema"},
    "en": {"book": "book", "chapter": "ch.", "section": "section", "problem": "problem"},
}
# Longest Bekker range string, reserved for the header while packing ("1106b36–1107a38").
_MAX_RANGE_CHARS = 16

Span = tuple[int, int]


class ChunkTooLongError(ValueError):
    """A chunk with its header exceeds settings.chunk_max_tokens."""


@dataclass(frozen=True)
class _Point:
    word: int  # word index the Bekker value applies from
    value: int  # encoded Bekker position
    real: bool  # a marker of the edition, not a unit boundary


def chunking_config(settings: Settings | None = None) -> dict:
    """Everything that changes the chunks; hashed into index_version."""
    s = settings or get_settings()
    return {
        "chunker_version": CHUNKER_VERSION,
        "min_words": s.chunk_min_words,
        "max_words": s.chunk_max_words,
        "overlap_words": s.chunk_overlap_words,
        "max_tokens": s.chunk_max_tokens,
        "chars_per_token": s.chars_per_token,
        "bekker_column_lines": s.bekker_column_lines,
    }


def estimate_tokens(text: str, settings: Settings | None = None) -> int:
    s = settings or get_settings()
    return math.ceil(len(text) / s.chars_per_token)


def chunk_units(
    units: Iterable[Unit], works: dict[str, Work], settings: Settings | None = None
) -> list[Chunk]:
    """Chunk every indexable unit. `seq` numbers chunks in order inside each edition."""
    settings = settings or get_settings()
    by_edition: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        if unit.text_type == TextType.aristotle:
            by_edition[unit.edition_id].append(unit)
    chunks: list[Chunk] = []
    for edition_id in sorted(by_edition):
        seq = 0
        for unit in sorted(by_edition[edition_id], key=lambda u: u.seq):
            if unit.work_id not in works:
                raise KeyError(f"unit {unit.unit_id}: work {unit.work_id!r} not in catalogue")
            unit_chunks = chunk_unit(unit, works[unit.work_id], settings, seq_start=seq)
            seq += len(unit_chunks)
            chunks.extend(unit_chunks)
    return chunks


def chunk_unit(
    unit: Unit, work: Work, settings: Settings | None = None, *, seq_start: int = 0
) -> list[Chunk]:
    """Cut one unit into chunks. Deterministic: same unit and settings, same chunks."""
    s = settings or get_settings()
    text = unit.text
    word_starts = [m.start() for m in _WORD_RE.finditer(text)]
    if not word_starts:
        return []

    header_prefix = _header_prefix(unit, work)
    # Characters left for the chunk text once the header (with the longest possible
    # Bekker range) and the newline after it are counted.
    char_budget = math.floor(s.chunk_max_tokens * s.chars_per_token) - (
        len(header_prefix) + 2 + _MAX_RANGE_CHARS + 1
    )
    if char_budget <= 0:
        raise ChunkTooLongError(
            f"La cabecera de {unit.unit_id} no deja lugar para texto con "
            f"chunk_max_tokens={s.chunk_max_tokens}."
        )

    def words_in(span: Span) -> int:
        return bisect_left(word_starts, span[1]) - bisect_left(word_starts, span[0])

    def fits(span: Span) -> bool:
        return words_in(span) <= s.chunk_max_words and span[1] - span[0] <= char_budget

    paragraphs = _paragraphs(text)
    atoms = [atom for p in paragraphs for atom in _atoms(text, p, fits)]
    spans = _pack(atoms, fits, words_in, s.chunk_min_words)
    shortest = min(words_in(span) for span in spans)
    if len(spans) > 1 and shortest < s.chunk_min_words:
        # Whole paragraphs left a short chunk: pack again from sentences, and keep that if
        # its shortest chunk is longer.
        sentences = [atom for p in paragraphs for atom in _atoms(text, p, fits, split=True)]
        finer = _pack(sentences, fits, words_in, s.chunk_min_words)
        if min(words_in(span) for span in finer) > shortest:
            spans = finer
    spans = _add_overlap(spans, word_starts, s.chunk_overlap_words)

    points = _bekker_points(unit, word_starts)
    col_lines = max([s.bekker_column_lines] + [_line_of(p.value) for p in points])
    total_words = len(word_starts)

    chunks: list[Chunk] = []
    for n, (start, end) in enumerate(spans, start=1):
        chunk_text = text[start:end]
        first_word = bisect_left(word_starts, start)
        after_last_word = bisect_left(word_starts, end)
        whole_unit = first_word == 0 and after_last_word == total_words
        b_start, b_end, precision = _chunk_bekker(
            unit, points, first_word, after_last_word, col_lines, whole_unit
        )
        header = _header(header_prefix, b_start, b_end)
        embed_text = f"{header}\n{chunk_text}"
        chunk_id = f"{unit.unit_id}#{n}"
        tokens = estimate_tokens(embed_text, s)
        if tokens > s.chunk_max_tokens:
            raise ChunkTooLongError(
                f"El fragmento {chunk_id} tiene ~{tokens} tokens con su cabecera "
                f"(máximo {s.chunk_max_tokens}). Bajar chunk_max_words o chunk_overlap_words."
            )
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                unit_id=unit.unit_id,
                work_id=unit.work_id,
                edition_id=unit.edition_id,
                lang=unit.lang,
                divisions=unit.divisions,
                seq=seq_start + n - 1,
                bekker_start=b_start,
                bekker_end=b_end,
                bekker_precision=precision,
                text=chunk_text,
                embed_text=embed_text,
            )
        )
    return chunks


# --- cutting ---------------------------------------------------------------------------


def _trim(text: str, start: int, end: int) -> Span | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _split_span(text: str, span: Span, pattern: re.Pattern) -> list[Span]:
    """Cut `span` at every match of `pattern`; the matched separator is dropped."""
    parts: list[Span] = []
    pos = span[0]
    for m in pattern.finditer(text, span[0], span[1]):
        piece = _trim(text, pos, m.start())
        if piece:
            parts.append(piece)
        pos = m.end()
    piece = _trim(text, pos, span[1])
    if piece:
        parts.append(piece)
    return parts


def _paragraphs(text: str) -> list[Span]:
    """Paragraphs are separated by blank lines; if there are none, by single newlines."""
    pattern = _BLANK_LINE_RE if _BLANK_LINE_RE.search(text) else _NEWLINE_RE
    return _split_span(text, (0, len(text)), pattern)


def _atoms(
    text: str, span: Span, fits: Callable[[Span], bool], level: int = 0, split: bool = False
) -> list[Span]:
    """Split a span that does not fit into smaller pieces at the finest needed boundary.
    `split`: cut at the boundaries of this level even when the span fits."""
    if fits(span) and not split:
        return [span]
    if level < len(_SPLIT_PATTERNS):
        out: list[Span] = []
        for part in _split_span(text, span, _SPLIT_PATTERNS[level]):
            out.extend(_atoms(text, part, fits, level + 1))
        return out
    # Last resort: cut between words.
    pieces: list[Span] = []
    current: Span | None = None
    for m in _WORD_RE.finditer(text, span[0], span[1]):
        w_start, w_end = m.start(), m.end()
        if current is None:
            current = (w_start, w_end)
        elif fits((current[0], w_end)):
            current = (current[0], w_end)
        else:
            pieces.append(current)
            current = (w_start, w_end)
    if current:
        pieces.append(current)
    return pieces


def _pack(
    atoms: list[Span],
    fits: Callable[[Span], bool],
    words_in: Callable[[Span], int],
    min_words: int,
) -> list[Span]:
    """Join consecutive atoms into the fewest chunks that fit, as even as possible.

    A greedy pass (join while it fits) gives the smallest number of chunks n. Among the
    ways to cut the atoms into n chunks that fit, the shortest chunk is made as long as
    possible, up to `min_words`; then the cut closest to total_words / n words per chunk
    (least squares) is taken. Both by dynamic programming over the atoms.
    """
    m = len(atoms)

    def words(i: int, j: int) -> int:  # atoms i..j-1 as one chunk
        return words_in((atoms[i][0], atoms[j - 1][1]))

    # Chunks that may end at atom j: starts i (nearest first) whose span still fits. A
    # longer span never fits where a shorter one does not, so the scan stops at the first
    # misfit; a single atom always fits (see _atoms).
    starts: list[list[int]] = [[] for _ in range(m + 1)]
    for j in range(1, m + 1):
        for i in range(j - 1, -1, -1):
            if i < j - 1 and not fits((atoms[i][0], atoms[j - 1][1])):
                break
            starts[j].append(i)

    n = 0  # greedy: the fewest chunks
    i = 0
    while i < m:
        j = i + 1
        while j < m and fits((atoms[i][0], atoms[j][1])):
            j += 1
        n, i = n + 1, j

    # Longest possible shortest chunk with n chunks: best[g][j] for atoms 0..j-1 in g chunks.
    best = [[-1] * (m + 1) for _ in range(n + 1)]
    best[0][0] = math.inf
    for g in range(1, n + 1):
        for j in range(1, m + 1):
            for i in starts[j]:
                if best[g - 1][i] >= 0:
                    best[g][j] = max(best[g][j], min(best[g - 1][i], words(i, j)))
    floor = min(best[n][m], min_words)

    # Least squares around the mean, every chunk at least `floor` words.
    target = words(0, m) / n
    cost = [[math.inf] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for g in range(1, n + 1):
        for j in range(1, m + 1):
            for i in starts[j]:
                w = words(i, j)
                if w >= floor and cost[g - 1][i] + (w - target) ** 2 < cost[g][j]:
                    cost[g][j] = cost[g - 1][i] + (w - target) ** 2
                    back[g][j] = i
    spans: list[Span] = []
    j = m
    for g in range(n, 0, -1):
        i = back[g][j]
        spans.append((atoms[i][0], atoms[j - 1][1]))
        j = i
    return spans[::-1]


def _add_overlap(spans: list[Span], word_starts: list[int], overlap_words: int) -> list[Span]:
    """Start every chunk but the first `overlap_words` words earlier (inside the unit)."""
    if overlap_words <= 0:
        return spans
    out = [spans[0]]
    for start, end in spans[1:]:
        first = bisect_left(word_starts, start)
        out.append((word_starts[max(0, first - overlap_words)], end))
    return out


# --- Bekker ranges ---------------------------------------------------------------------


def _line_of(value: int) -> int:
    rest = value % 200
    return rest - 100 if rest >= 100 else rest


def _bekker_points(unit: Unit, word_starts: list[int]) -> list[_Point]:
    """Anchors as (word index, value), plus the unit range at both ends of the text.

    Anchors are clamped to the unit range; an anchor that goes backwards is dropped, and
    when several share a word the last one wins (a page marker is followed by its line 1).
    """
    lo, hi = unit.bekker_start, unit.bekker_end
    by_word: dict[int, _Point] = {}
    if lo is not None:
        by_word[0] = _Point(0, lo, real=False)
    running = lo
    for offset, value in sorted(unit.bekker_anchors, key=lambda a: a[0]):
        if lo is not None:
            value = max(value, lo)
        if hi is not None:
            value = min(value, hi)
        if running is not None and value < running:
            continue
        running = value
        word = bisect_left(word_starts, offset)
        by_word[word] = _Point(word, value, real=True)
    if hi is not None:
        end_word = len(word_starts)
        last = by_word.get(end_word)
        if last is None or not last.real:
            by_word[end_word] = _Point(end_word, hi, real=False)
    return [by_word[w] for w in sorted(by_word)]


def _column_index(value: int) -> int:
    """Running column number of an encoded Bekker value (page * 2, plus 1 for column b)."""
    page, rest = divmod(value, 200)
    return page * 2 + (1 if rest >= 100 else 0)


def _to_linear(value: int, col_lines: int) -> int:
    """Encoded Bekker -> running line number, with `col_lines` lines per column."""
    return _column_index(value) * col_lines + _line_of(value)


def _from_linear(x: int, col_lines: int) -> int:
    column, line = divmod(x, col_lines)
    if line == 0:  # line 0 of a column is the last line of the previous one
        column, line = column - 1, col_lines
    page, col = divmod(column, 2)
    return bekker.encode(page, "b" if col else "a", line)


def _value_at(
    points: list[_Point], word: int, col_lines: int, round_fn: Callable[[float], int]
) -> tuple[int, bool]:
    """Bekker value at a word index and whether it sits on a real marker."""
    words = [p.word for p in points]
    i = bisect_right(words, word) - 1
    if i >= 0 and points[i].word == word:
        return points[i].value, points[i].real
    if i < 0:
        return points[0].value, False
    if i == len(points) - 1:
        return points[-1].value, False
    a, b = points[i], points[i + 1]
    frac = (word - a.word) / (b.word - a.word)
    xa, xb = _to_linear(a.value, col_lines), _to_linear(b.value, col_lines)
    value = _from_linear(round_fn(xa + frac * (xb - xa)), col_lines)
    return min(max(value, a.value), b.value), False


def _chunk_bekker(
    unit: Unit,
    points: list[_Point],
    first_word: int,
    after_last_word: int,
    col_lines: int,
    whole_unit: bool,
) -> tuple[int | None, int | None, BekkerPrecision]:
    if not points:
        return None, None, BekkerPrecision.none
    has_markers = any(p.real for p in points)
    if not has_markers and unit.bekker_precision not in (
        BekkerPrecision.line,
        BekkerPrecision.interpolated,
    ):
        # Only a page- or chapter-level range: interpolating lines would invent precision.
        return unit.bekker_start, unit.bekker_end, unit.bekker_precision
    start, start_real = _value_at(points, first_word, col_lines, math.floor)
    # The end is the position where the next chunk starts. Interpolated, it is shared with
    # the next chunk (the line break between them is unknown). On a real marker, the next
    # chunk starts that line, so this one ends on the line before: exact inside a column,
    # assumed (col_lines) when the marker opens a column.
    end, end_real = _value_at(points, after_last_word, col_lines, math.ceil)
    if end_real:
        if _line_of(end) >= 2:
            end -= 1
        else:
            end = _from_linear(_column_index(end) * col_lines, col_lines)
            end_real = False
    if unit.bekker_start is not None:
        start = max(start, unit.bekker_start)
    if unit.bekker_end is not None:
        end = min(end, unit.bekker_end)
    end = max(end, start)
    if start_real and end_real:
        precision = BekkerPrecision.line
    elif whole_unit and unit.bekker_precision != BekkerPrecision.none:
        precision = unit.bekker_precision
    else:
        precision = BekkerPrecision.interpolated
    return start, end, precision


# --- header ----------------------------------------------------------------------------


def _roman(n: int) -> str:
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


def _header_lang(lang: Lang) -> str:
    return "es" if lang == Lang.es else "en"


def _header_prefix(unit: Unit, work: Work) -> str:
    lang = _header_lang(unit.lang)
    title = work.title_es if lang == "es" else work.title_en
    parts = [_AUTHOR[lang], title]
    for level, label in unit.divisions:
        if level in _HEADER_SKIP_LEVELS:
            continue
        word = _LEVEL_WORDS[lang].get(level, level)
        if level == "book" and label.isdigit():
            label = _roman(int(label))
        parts.append(f"{word} {label}")
    return ", ".join(parts)


def _header(prefix: str, start: int | None, end: int | None) -> str:
    if start is None:
        return prefix
    return f"{prefix}, {bekker.format_range(start, end)}"
