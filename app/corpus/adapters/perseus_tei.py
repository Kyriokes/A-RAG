"""Adapter for Perseus TEI XML (PerseusDL/canonical-greekLit).

What it does, in two steps:

1. ``parse_tei`` walks the XML in document order and returns one ``ParsedUnit`` per
   division at the unit depth (the chapter level of the work's structure). Deeper
   divisions (Loeb sections and subsections) are merged into their unit. The division
   types are read from the TEI ``subtype`` attributes, never assumed. Translator notes and
   loose bibliographic references go to ``notes``; book headings are dropped. While walking
   it carries the running Bekker position and records every page and line marker.

2. ``unit_spans`` turns the markers into a Bekker range per unit, and ``adapt`` builds
   ``app.models.Unit`` objects. For a translation the chapter ranges come from the Greek
   text of the same work (markers every 5 lines), matched by division path; the
   translation's own markers (about every 20 lines) become ``bekker_anchors``.

Bekker encodings handled (PLAN, "Notas sobre lo verificado en Perseus"):

- ``<milestone unit="page" resp="Bekker" n="1094a"/>`` (most files);
- ``<milestone unit="section" n="1214a"/>`` (Eudemian Ethics, English);
- ``<div subtype="bekker_page" n="1252a">`` with an inner page milestone repeating the same
  page (Politics); a page that repeats the current one never resets the line;
- line markers ``<milestone unit="line" n="20"/>`` with ``resp="Bekker"`` or ``ed="Bekker"``;
- works that do not start at line 1 (Metaphysics 980a21, Poetics 1447a8): a page marker
  implies line 1 only until a line marker at the same point overrides it.

Other milestones (Loeb or Ross section numbers, e.g. ``n="loeb_section_1"``) are ignored.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from app import bekker
from app.corpus.catalog import STRUCTURE_LEVELS
from app.models import BekkerPrecision, Edition, TextType, Unit, Work
from app.settings import Settings, get_settings

# Private-use sentinels placed in the raw text stream while walking the XML; _finalize
# turns them into offsets and whitespace. Nothing in the sources uses this range.
MARK = "\ue000"  # a Bekker marker
PARA = "\ue001"  # paragraph break
LINE = "\ue002"  # line break (verse line, <lb/>)
# Element boundary: becomes a space only between two glued words, because the sources
# often omit it around inline elements ("in the<title>Ethics</title>").
SOFT = "\ue003"
_NO_SENTINELS = {ord(c): None for c in (MARK, PARA, LINE, SOFT)}

_PAGE_RE = re.compile(r"^(\d{1,4})\s*([ab])$", re.IGNORECASE)
_LINE_RE = re.compile(r"^\d{1,2}$")

# Block-level elements: a paragraph break before and after.
_BLOCK_TAGS = {"div", "p", "lg", "list", "item", "sp", "table", "row", "ab"}
# Elements whose content is not part of the work's text (the tail still is).
_SKIP_TAGS = {"head", "figure", "fw"}
# Inside <choice>, the reading we keep.
_CHOICE_PREFERRED = ("corr", "reg", "expan")
# Editorial inline elements that can sit inside a word: no word boundary around them.
_EDITORIAL_TAGS = {"add", "del", "supplied", "unclear", "corr", "reg", "expan", "w", "c", "pc"}
_QUOTE_MARKS = {"single": ("‘", "’"), "double": ("“", "”")}
# Literal angle brackets are editorial supplements (e.g. Eudemian Ethics "<will be ...>");
# they become the conventional ⟨ ⟩ so no "<" survives in the text.
_CHAR_MAP = str.maketrans({"<": "⟨", ">": "⟩", " ": " ", "­": None, "​": None, "﻿": None})
_GAP_TEXT = "…"
# No space before closing punctuation or after an opening quote (removing a note or the
# padding inside <q> leaves "word , and" or "“ Socrates”"). "." is left alone: ". . .".
_NO_SPACE_BEFORE = set(",;:?!”)")
_NO_SPACE_AFTER = set("‘“(")


def _local(el: etree._Element) -> str | None:
    if not isinstance(el.tag, str):
        return None  # comment or processing instruction
    return etree.QName(el).localname


@dataclass
class _RawMarker:
    kind: str  # "page" | "line"
    value: str


@dataclass
class _UnitBuffer:
    path: tuple[str, ...]
    sourceline: int | None
    parts: list[str] = field(default_factory=list)
    markers: list[_RawMarker] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ParsedUnit:
    path: tuple[str, ...]  # division labels, outermost first, e.g. ("2", "6")
    sourceline: int | None  # line of the unit's <div> in the XML file
    text: str
    notes: list[str]
    anchors: list[tuple[int, int]]  # (char offset in text, encoded Bekker position)
    word_start: int  # index of the unit's first word in the document's word stream
    word_end: int  # one past the unit's last word


@dataclass
class ParsedText:
    level_types: list[str]  # TEI subtype of each textpart level, outermost first
    unit_depth: int
    units: list[ParsedUnit]
    markers: list[tuple[int, int]]  # (word index, encoded position), document order
    header: dict[str, str]
    warnings: list[str]


@dataclass(frozen=True)
class Span:
    start: int | None
    end: int | None
    exact_start: bool = False
    exact_end: bool = False


@dataclass
class AdaptResult:
    units: list[Unit]
    warnings: list[str]
    header: dict[str, str]
    level_types: list[str]


# --- step 1: walk the XML ------------------------------------------------------------------


class _Walker:
    def __init__(self, unit_depth: int) -> None:
        self.unit_depth = unit_depth
        self.level_types: list[str] = []
        self.path: list[str] = []
        self.units: list[_UnitBuffer] = []
        self.current: _UnitBuffer | None = None
        self.pending: list[_RawMarker] = []  # markers met between units
        self.warnings: list[str] = []
        self.orphan_words = 0

    # output helpers
    def emit(self, text: str | None) -> None:
        if not text:
            return
        if self.current is not None:
            self.current.parts.append(text)
        else:
            visible = text.translate(_NO_SENTINELS).split()
            self.orphan_words += len(visible)

    def marker(self, kind: str, value: str) -> None:
        m = _RawMarker(kind, value)
        if self.current is not None:
            # A marker sits between words: "of<milestone/>Ausonians" is two words.
            self.current.parts.append(SOFT + MARK)
            self.current.markers.append(m)
        else:
            self.pending.append(m)

    def note(self, text: str) -> None:
        text = text.strip()
        if text and self.current is not None:
            self.current.notes.append(text)

    # traversal
    def walk_children(self, el: etree._Element) -> None:
        self.emit(el.text)
        for child in el:
            self.walk(child)

    def walk(self, el: etree._Element) -> None:
        tag = _local(el)
        if tag is None:
            pass
        elif tag == "div" and el.get("type") == "textpart":
            self._div(el)
        elif tag == "milestone":
            self._milestone(el)
        elif tag in _SKIP_TAGS:
            pass
        elif tag in ("note", "bibl"):
            # <bibl> outside a note is the translator's source reference for a quotation.
            self.note(inline_text(el))
            self.emit(SOFT)  # "first<note/>2" must not become "first2"
        elif tag == "choice":
            chosen = _choice_reading(el)
            if chosen is not None:
                self.walk_children(chosen)
        elif tag == "gap":
            self.emit(f" {_GAP_TEXT} ")
        elif tag == "lb":
            self.emit(LINE)
        elif tag == "q":
            open_mark, close_mark = _QUOTE_MARKS.get(el.get("rend") or "", ("", ""))
            self.emit(SOFT + open_mark)
            self.walk_children(el)
            self.emit(close_mark + SOFT)
        elif tag == "l":
            self.emit(LINE)
            self.walk_children(el)
            self.emit(LINE)
        elif tag in _BLOCK_TAGS or (tag == "quote" and _is_block_quote(el)):
            self.emit(PARA)
            self.walk_children(el)
            self.emit(PARA)
        elif tag in _EDITORIAL_TAGS:  # may sit inside a word
            self.walk_children(el)
        else:
            self.emit(SOFT)
            self.walk_children(el)
            self.emit(SOFT)
        self.emit(el.tail)

    def _div(self, el: etree._Element) -> None:
        depth = len(self.path) + 1
        subtype = (el.get("subtype") or "").strip() or "?"
        if len(self.level_types) < depth:
            self.level_types.append(subtype)
        elif self.level_types[depth - 1] != subtype:
            self.warnings.append(
                f"línea {el.sourceline}: división de nivel {depth} con tipo {subtype!r}, "
                f"se esperaba {self.level_types[depth - 1]!r}"
            )
        label = (el.get("n") or "").strip()
        self.path.append(label)
        is_unit = depth == self.unit_depth
        if is_unit:
            self._open_unit(el)
        # Politics: the division itself is a Bekker page. When it also holds the page's
        # milestone, the milestone is the precise point and the division is not counted.
        if "page" in subtype.lower() and _PAGE_RE.match(label) and not _holds_page(el, label):
            self.marker("page", label)
        self.walk_children(el)
        if is_unit:
            self.units.append(self.current)
            self.current = None
        self.path.pop()

    def _open_unit(self, el: etree._Element) -> None:
        if self.current is not None:  # a unit nested in a unit: should not happen
            self.warnings.append(f"línea {el.sourceline}: unidad anidada dentro de otra")
            self.units.append(self.current)
        self.current = _UnitBuffer(path=tuple(self.path), sourceline=el.sourceline)
        for m in self.pending:
            self.current.parts.append(MARK)
            self.current.markers.append(m)
        self.pending = []

    def _milestone(self, el: etree._Element) -> None:
        unit = (el.get("unit") or "").strip().lower()
        n = (el.get("n") or "").strip()
        who = (el.get("resp") or el.get("ed") or "").strip().lower()
        if unit == "line" and who == "bekker" and _LINE_RE.match(n):
            self.marker("line", n)
        elif unit in ("page", "section") and who in ("bekker", "") and _PAGE_RE.match(n):
            self.marker("page", n)


def _choice_reading(choice: etree._Element) -> etree._Element | None:
    """The child of <choice> we keep: the corrected, regularised or expanded reading."""
    children = [c for c in choice if _local(c) is not None]
    preferred = [c for c in children if _local(c) in _CHOICE_PREFERRED]
    return (preferred or children or [None])[0]


def _holds_page(div: etree._Element, label: str) -> bool:
    for m in div.iter("{http://www.tei-c.org/ns/1.0}milestone"):
        unit = (m.get("unit") or "").strip().lower()
        if unit in ("page", "section") and (m.get("n") or "").strip() == label:
            return True
    return False


def _is_block_quote(el: etree._Element) -> bool:
    rend = (el.get("rend") or "").lower()
    if "block" in rend:
        return True
    return any(_local(c) in ("l", "lg", "p") for c in el.iter())


def inline_text(el: etree._Element) -> str:
    """Plain text of an element (used for notes): quotes rendered, whitespace collapsed."""
    parts: list[str] = []

    def rec(node: etree._Element) -> None:
        tag = _local(node)
        if tag == "choice":
            chosen = _choice_reading(node)
            if chosen is not None:
                parts.append(chosen.text or "")
                for child in chosen:
                    rec(child)
        elif tag is not None:
            if tag == "gap":
                parts.append(f" {_GAP_TEXT} ")
            else:
                open_mark, close_mark = ("", "")
                if tag == "q":
                    open_mark, close_mark = _QUOTE_MARKS.get(node.get("rend") or "", ("", ""))
                soft = "" if tag in _EDITORIAL_TAGS else SOFT
                parts.append(soft + open_mark)
                parts.append(node.text or "")
                for child in node:
                    rec(child)
                parts.append(close_mark + soft)
        parts.append(node.tail or "")

    parts.append(el.text or "")
    for child in el:
        rec(child)
    return _finalize("".join(parts))[0]


def _ends_word(ch: str) -> bool:
    return ch.isalnum() or ch in ",;:’”"


def _starts_word(ch: str) -> bool:
    return ch.isalnum() or ch in "‘“("


def _finalize(raw: str) -> tuple[str, list[int]]:
    """Collapse whitespace and resolve sentinels.

    Returns the clean text and, for each MARK in order, the offset of the first visible
    character that follows it (or the text length when nothing follows).
    """
    raw = raw.translate(_CHAR_MAP)
    out: list[str] = []
    n = 0
    offsets: list[int] = []
    pending_marks = 0
    sep = ""  # pending separator: "", " ", "\n" or "\n\n"
    sep_newline = False  # the source whitespace contained a newline
    soft = False  # an element boundary since the last visible character
    for i, ch in enumerate(raw):
        if ch == MARK:
            pending_marks += 1
            continue
        if ch == PARA:
            sep = "\n\n"
            continue
        if ch == LINE:
            if sep != "\n\n":
                sep = "\n"
            continue
        if ch == SOFT:
            soft = True
            continue
        if ch.isspace():
            if not sep:
                sep = " "
            if ch == "\n":
                sep_newline = True
            continue
        if out:
            if soft and not sep and _ends_word(out[-1]) and _starts_word(ch):
                sep = " "  # "in the<title>Ethics</title>" -> "in the Ethics"
            if sep == " " and (
                ch in _NO_SPACE_BEFORE
                or out[-1] in _NO_SPACE_AFTER
                or (ch == "." and out[-1] != "." and not raw.startswith((". .", ".."), i))
            ):
                sep = ""  # "disposition<note/> , and" -> "disposition, and"; ". . ." stays
            if (
                sep == " "
                and sep_newline
                and out[-1] == "-"
                and len(out) > 1
                and out[-2].isalpha()
                and ch.islower()
            ):
                sep = ""  # a word split by a source line break after its hyphen
            if sep:
                out.append(sep)
                n += len(sep)
        offsets.extend([n] * pending_marks)
        pending_marks = 0
        out.append(ch)
        n += 1
        sep = ""
        sep_newline = False
        soft = False
    offsets.extend([n] * pending_marks)
    return "".join(out), offsets


def _read_header(root: etree._Element) -> dict[str, str]:
    """Facts from the TEI header that the manifest records (for checking, not trust)."""
    ns = {"t": "http://www.tei-c.org/ns/1.0"}

    def first(xpath: str) -> str:
        found = root.xpath(xpath, namespaces=ns)
        if not found:
            return ""
        node = found[0]
        value = node if isinstance(node, str) else "".join(node.itertext())
        return " ".join(value.split())

    return {
        "title": first("//t:sourceDesc//t:monogr/t:title"),
        "editor": first("//t:sourceDesc//t:monogr/t:editor"),
        "editor_role": first("//t:sourceDesc//t:monogr/t:editor/@role"),
        "date": first("//t:sourceDesc//t:imprint/t:date"),
        "licence_target": first("//t:publicationStmt//t:licence/@target"),
        "licence_text": first("//t:publicationStmt//t:licence"),
    }


class _Running:
    """The running Bekker position while markers are met in document order."""

    def __init__(self, warnings: list[str]) -> None:
        self.page: int | None = None
        self.column: str | None = None
        self.line = 0
        self.warnings = warnings

    def apply(self, m: _RawMarker, where: str) -> int | None:
        if m.kind == "page":
            match = _PAGE_RE.match(m.value)
            page, column = int(match.group(1)), match.group(2).lower()
            if (page, column) == (self.page, self.column):
                return None  # the same page marked twice: keep the running line
            if self.page is not None and (page, column) < (self.page, self.column):
                self.warnings.append(
                    f"{where}: la página {page}{column} retrocede "
                    f"(venía de {self.page}{self.column})"
                )
            self.page, self.column, self.line = page, column, 1
        else:
            if self.page is None:
                self.warnings.append(f"{where}: marca de línea {m.value} antes de toda página")
                return None
            line = int(m.value)
            if not 1 <= line <= bekker.MAX_LINE:
                self.warnings.append(f"{where}: línea fuera de rango {m.value}")
                return None
            if line < self.line:
                self.warnings.append(
                    f"{where}: la línea retrocede en {self.page}{self.column} "
                    f"({self.line} -> {line})"
                )
            self.line = line
        return bekker.encode(self.page, self.column, self.line)


def parse_tei(path: Path, unit_depth: int) -> ParsedText:
    """Parse a Perseus TEI file into units at `unit_depth` (1 = outermost textpart)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    root = etree.parse(str(path), parser).getroot()
    body = root.find(".//{http://www.tei-c.org/ns/1.0}body")
    if body is None:
        raise ValueError(f"{path}: el TEI no tiene <body>")
    walker = _Walker(unit_depth)
    walker.walk_children(body)
    warnings = list(walker.warnings)
    if walker.orphan_words:
        warnings.append(f"{walker.orphan_words} palabras fuera de toda unidad (descartadas)")
    if len(walker.level_types) < unit_depth:
        raise ValueError(
            f"{path}: el TEI tiene {len(walker.level_types)} niveles de división "
            f"({' > '.join(walker.level_types)}), la estructura pide {unit_depth}"
        )

    running = _Running(warnings)
    units: list[ParsedUnit] = []
    markers: list[tuple[int, int]] = []
    words_before = 0
    carried: list[int] = []  # markers after the last word of the previous unit
    for index, buf in enumerate(walker.units):
        text, offsets = _finalize("".join(buf.parts))
        where = ".".join(buf.path)
        anchors: list[tuple[int, int]] = [(0, carried[-1])] if carried else []
        carried = []
        for m, offset in zip(buf.markers, offsets, strict=True):
            pos = running.apply(m, where)
            if pos is None:
                continue
            word = words_before + len(text[:offset].split())
            if offset >= len(text) and index + 1 < len(walker.units):
                # Nothing of this unit follows the marker: it marks the next unit's start.
                carried.append(pos)
            # Several markers at one point (page, then its line): the last one wins.
            elif anchors and anchors[-1][0] == offset:
                anchors[-1] = (offset, pos)
            else:
                anchors.append((offset, pos))
            if markers and markers[-1][0] == word:
                markers[-1] = (word, pos)
            else:
                markers.append((word, pos))
        n_words = len(text.split())
        units.append(
            ParsedUnit(
                path=buf.path,
                sourceline=buf.sourceline,
                text=text,
                notes=buf.notes,
                anchors=anchors,
                word_start=words_before,
                word_end=words_before + n_words,
            )
        )
        words_before += n_words
    return ParsedText(
        level_types=walker.level_types,
        unit_depth=unit_depth,
        units=units,
        markers=markers,
        header=_read_header(root),
        warnings=warnings,
    )


# --- step 2: Bekker ranges -----------------------------------------------------------------


def _split(value: int) -> tuple[int, str, int]:
    ref = bekker.decode(value)
    return ref.page, ref.column, ref.line or 0


def _column_index(page: int, column: str) -> int:
    return page * 2 + (1 if column == "b" else 0)


def _from_column_index(index: int) -> tuple[int, str]:
    page, rest = divmod(index, 2)
    return page, "b" if rest else "a"


def _words_per_line(markers: list[tuple[int, int]]) -> float | None:
    """Average words per Bekker line, from consecutive markers inside one column."""
    words = lines = 0
    for (w0, p0), (w1, p1) in zip(markers, markers[1:], strict=False):
        a, b = _split(p0), _split(p1)
        if a[:2] == b[:2] and b[2] > a[2] and w1 > w0:
            words += w1 - w0
            lines += b[2] - a[2]
    return words / lines if lines else None


def position_at(
    markers: list[tuple[int, int]],
    word: int,
    *,
    column_lines: int,
    words_per_line: float | None,
) -> tuple[int | None, bool]:
    """Bekker position of a word of the stream, and whether a marker sits exactly on it.

    Between two markers the line is interpolated by word count; across a column break each
    column counts as `column_lines` lines (or up to its last marked line, if higher). After
    the last marker the line is extrapolated with the document's words per line.
    """
    words = [w for w, _ in markers]
    i = bisect_right(words, word) - 1
    if i < 0:
        return None, False
    w0, p0 = markers[i]
    if w0 == word:
        return p0, True
    page0, col0, line0 = _split(p0)
    if i + 1 < len(markers):
        w1, p1 = markers[i + 1]
        page1, col1, line1 = _split(p1)
        c0, c1 = _column_index(page0, col0), _column_index(page1, col1)
        if c1 == c0 and line1 > line0:
            line = line0 + (word - w0) * (line1 - line0) // (w1 - w0)
            return bekker.encode(page0, col0, line), False
        if c1 > c0:
            first_len = max(column_lines, line0)
            gap = (first_len - line0) + (c1 - c0 - 1) * column_lines + line1
            step = (word - w0) * gap // (w1 - w0)
            if line0 + step <= first_len:
                return bekker.encode(page0, col0, max(line0 + step, 1)), False
            step -= first_len - line0
            col = c0 + 1
            while step > column_lines and col < c1:
                step -= column_lines
                col += 1
            page, column = _from_column_index(col)
            return bekker.encode(page, column, max(1, min(step, bekker.MAX_LINE))), False
        # Markers out of order: fall through to extrapolation from the last one.
    if words_per_line:
        line = line0 + int((word - w0) / words_per_line)
        line = min(line, max(column_lines, line0), bekker.MAX_LINE)
        return bekker.encode(page0, col0, line), False
    return p0, False


def unit_spans(parsed: ParsedText, settings: Settings | None = None) -> dict[tuple, Span]:
    """Bekker range of every unit, keyed by division path.

    A unit starts where its first word is (exact when a marker sits on it). It ends where
    the next unit of the same parent starts, one line before, when that start is an exact
    line marker; otherwise at the interpolated position of its own last word.
    """
    settings = settings or get_settings()
    column_lines = settings.bekker_column_lines
    rate = _words_per_line(parsed.markers)
    spans: dict[tuple, Span] = {}
    units = parsed.units
    for i, unit in enumerate(units):
        start, exact_start = position_at(
            parsed.markers, unit.word_start, column_lines=column_lines, words_per_line=rate
        )
        if start is None:
            spans[unit.path] = Span(None, None)
            continue
        end: int | None = None
        exact_end = False
        nxt = units[i + 1] if i + 1 < len(units) else None
        if nxt is not None and nxt.path[:-1] == unit.path[:-1] and unit.word_end > unit.word_start:
            nxt_start, nxt_exact = position_at(
                parsed.markers, nxt.word_start, column_lines=column_lines, words_per_line=rate
            )
            if nxt_exact and nxt_start is not None and _split(nxt_start)[2] > 1:
                end, exact_end = nxt_start - 1, True
        if end is None:
            last_word = max(unit.word_start, unit.word_end - 1)
            end, _ = position_at(
                parsed.markers, last_word, column_lines=column_lines, words_per_line=rate
            )
            exact_end = exact_start and unit.word_end <= unit.word_start
        if end is not None and end < start:
            end = start
        spans[unit.path] = Span(start, end, exact_start, exact_end)
    return spans


# --- step 3: Units -------------------------------------------------------------------------


def _precision(span: Span) -> BekkerPrecision:
    if span.start is None:
        return BekkerPrecision.none
    if span.exact_start and span.exact_end:
        return BekkerPrecision.line
    return BekkerPrecision.interpolated


def _reconcile(units: list[ParsedUnit], spans: list[Span], own: dict[tuple, Span]) -> list[str]:
    """Fix the boundaries where the translation divides chapters unlike the Greek text.

    A marker of the translation that falls outside its unit's Greek range shows that the
    translation puts that chapter boundary elsewhere (e.g. NE VI.1|2, where Rackham's
    chapter 1 runs past 1139a1 but Bywater's chapter 2 starts at 1138b35). That boundary
    is then taken from the translation's own markers, interpolated. Edits `spans` in place.
    """
    messages: list[str] = []
    boundaries: set[int] = set()
    for i, (pu, span) in enumerate(zip(units, spans, strict=True)):
        if span.start is None or span.end is None:
            continue
        positions = [pos for _, pos in pu.anchors]
        if i > 0 and any(pos < span.start for pos in positions):
            boundaries.add(i - 1)
        if i + 1 < len(units) and any(pos > span.end for pos in positions):
            boundaries.add(i)
    for i in sorted(boundaries):
        a, b = units[i], units[i + 1]
        own_end, own_start = own[a.path].end, own[b.path].start
        sa, sb = spans[i], spans[i + 1]
        if None in (own_end, own_start, sa.start, sb.end):
            continue
        spans[i] = Span(min(sa.start, own_end), own_end, sa.exact_start, False)
        spans[i + 1] = Span(own_start, max(sb.end, own_start), False, sb.exact_end)
        messages.append(
            f"límite {'.'.join(a.path)}|{'.'.join(b.path)}: la traducción divide distinto "
            f"que el griego; se toma de sus propias marcas "
            f"({bekker.format_int(own_end)} | {bekker.format_int(own_start)})"
        )
    for pu, span in zip(units, spans, strict=True):
        if span.start is None or span.end is None:
            continue
        outside = [pos for _, pos in pu.anchors if not span.start <= pos <= span.end]
        if outside:
            messages.append(
                f"{'.'.join(pu.path)}: {len(outside)} marcas fuera del rango "
                f"{bekker.format_range(span.start, span.end)}"
            )
    return messages


def adapt(
    raw_path: Path,
    edition: Edition,
    work: Work,
    reference_path: Path | None = None,
    settings: Settings | None = None,
) -> AdaptResult:
    """Parse one edition into Units; chapter ranges come from `reference_path` if given."""
    levels = STRUCTURE_LEVELS[work.structure]
    parsed = parse_tei(raw_path, unit_depth=len(levels))
    warnings = list(parsed.warnings)
    own_spans = unit_spans(parsed, settings)
    ref_spans: dict[tuple, Span] = {}
    if reference_path is not None:
        reference = parse_tei(reference_path, unit_depth=len(levels))
        ref_spans = unit_spans(reference, settings)
        missing = [".".join(u.path) for u in parsed.units if u.path not in ref_spans]
        if missing:
            warnings.append(
                f"{len(missing)} unidades sin equivalente en el griego "
                f"(se usa el propio texto): {', '.join(missing[:10])}"
            )

    spans = [ref_spans.get(pu.path) or own_spans[pu.path] for pu in parsed.units]
    if ref_spans:
        warnings.extend(_reconcile(parsed.units, spans, own_spans))

    units: list[Unit] = []
    seen: set[tuple] = set()
    for seq, (pu, span) in enumerate(zip(parsed.units, spans, strict=True), start=1):
        if pu.path in seen:
            warnings.append(f"división repetida {'.'.join(pu.path)}")
        seen.add(pu.path)
        if not edition.has_bekker:
            span = Span(None, None)
        units.append(
            Unit(
                unit_id=f"{edition.edition_id}:{'.'.join(pu.path)}",
                work_id=work.work_id,
                edition_id=edition.edition_id,
                lang=edition.lang,
                divisions=list(zip(levels, pu.path, strict=True)),
                seq=seq,
                bekker_start=span.start,
                bekker_end=span.end,
                bekker_precision=_precision(span),
                bekker_anchors=pu.anchors if edition.has_bekker else [],
                text_type=TextType.aristotle,
                text=pu.text,
                notes=pu.notes,
            )
        )
    return AdaptResult(
        units=units, warnings=warnings, header=parsed.header, level_types=parsed.level_types
    )
