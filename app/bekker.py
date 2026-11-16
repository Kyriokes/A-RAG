"""Bekker references: parsing, integer encoding and ranges.

A Bekker reference is page + column + line of the 1831 Berlin edition, e.g. ``1106b36``.
It is encoded as one order-preserving integer::

    page * 200 + (100 if column == "b" else 0) + line

so ``1106b36`` -> ``221336``. Lines never exceed 99, so columns never collide.
A range is a pair of integers ``(start, end)``, both inclusive.

This module is a shared contract: the corpus adapters, chunking, retrieval, the
citation builder and the eval labels all use it. Do not change the encoding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_REF_RE = re.compile(r"^\s*(\d{1,4})\s*([ab])\s*(\d{1,2})?\s*$", re.IGNORECASE)

# Bekker refs written inside free text, e.g. "¿qué dice en 1097b22?" or "1097b22-1098a20".
# Pages 1-1462; we require 3-4 digits to avoid matching ordinary numbers like "5a".
# Not a column letter: a Spanish era marker after a year ("1150 a.C.", "384 a. C.",
# "a. de C.", "a. de J. C.", "a. n. e."), or "a" between two years ("de 1100 a 1200": a
# line has at most two digits).
_NOT_A_COLUMN = r"(?!\.?\s*(?:(?:de\s+)?(?:J\.?\s*)?C|n\.?\s*e)(?:\.|\b))(?!\s*\d{3})"
_REF_IN_TEXT_RE = re.compile(
    r"(?<![\w.])(\d{3,4})\s?([ab])" + _NOT_A_COLUMN + r"\s?(\d{1,2})?"
    r"(?:\s*[-–—]\s*(?:(\d{3,4})\s?([ab]))?\s?(\d{1,2})?)?(?![\w])",
    re.IGNORECASE,
)

MAX_PAGE = 1462
MAX_LINE = 99


class BekkerError(ValueError):
    """Raised for a malformed or out-of-range Bekker reference."""


@dataclass(frozen=True, order=True)
class BekkerRef:
    page: int
    column: str  # "a" or "b"
    line: int | None = None  # None when only page+column is known

    def __post_init__(self) -> None:
        if not 1 <= self.page <= MAX_PAGE:
            raise BekkerError(f"page out of range: {self.page}")
        if self.column not in ("a", "b"):
            raise BekkerError(f"column must be 'a' or 'b': {self.column!r}")
        if self.line is not None and not 1 <= self.line <= MAX_LINE:
            raise BekkerError(f"line out of range: {self.line}")

    def __str__(self) -> str:
        return f"{self.page}{self.column}{self.line if self.line is not None else ''}"

    def to_int(self, *, default_line: int = 1) -> int:
        """Integer encoding. A ref without a line uses ``default_line``."""
        line = self.line if self.line is not None else default_line
        return encode(self.page, self.column, line)


def encode(page: int, column: str, line: int) -> int:
    column = column.lower()
    if not 1 <= page <= MAX_PAGE or column not in ("a", "b") or not 0 <= line <= MAX_LINE:
        raise BekkerError(f"invalid Bekker components: {page}{column}{line}")
    return page * 200 + (100 if column == "b" else 0) + line


def decode(value: int) -> BekkerRef:
    page, rest = divmod(value, 200)
    column = "b" if rest >= 100 else "a"
    line = rest - 100 if column == "b" else rest
    return BekkerRef(page, column, line if line > 0 else None)


def format_int(value: int) -> str:
    return str(decode(value))


def parse(text: str) -> BekkerRef:
    """Parse one reference like ``1094a``, ``1094a1`` or ``1106 b 36``."""
    m = _REF_RE.match(text)
    if not m:
        raise BekkerError(f"not a Bekker reference: {text!r}")
    page, column, line = m.groups()
    return BekkerRef(int(page), column.lower(), int(line) if line else None)


def column_start(page: int, column: str) -> int:
    """First encoded position of a column (line 0, before line 1)."""
    return encode(page, column, 0)


def column_end(page: int, column: str) -> int:
    """Last encoded position of a column."""
    return encode(page, column, MAX_LINE)


def parse_range(text: str) -> tuple[int, int]:
    """Parse ``1106b36-1107a8``, ``1106b36-40`` (same column) or a single ref.

    A ref without a line covers its whole column.
    """
    parts = re.split(r"\s*[-–—]\s*", text.strip())
    if len(parts) == 1:
        ref = parse(parts[0])
        if ref.line is None:
            return column_start(ref.page, ref.column), column_end(ref.page, ref.column)
        v = ref.to_int()
        return v, v
    if len(parts) != 2:
        raise BekkerError(f"not a Bekker range: {text!r}")
    start = parse(parts[0])
    end_txt = parts[1]
    if re.fullmatch(r"\d{1,2}", end_txt):  # "1106b36-40"
        if start.line is None:
            raise BekkerError(f"line-only end needs a start line: {text!r}")
        end = BekkerRef(start.page, start.column, int(end_txt))
    else:
        end = parse(end_txt)
    s = start.to_int() if start.line is not None else column_start(start.page, start.column)
    e = end.to_int() if end.line is not None else column_end(end.page, end.column)
    if e < s:
        raise BekkerError(f"range end before start: {text!r}")
    return s, e


def find_refs(text: str) -> list[tuple[int, int]]:
    """Find Bekker refs or ranges mentioned in free text; returns encoded ranges."""
    out: list[tuple[int, int]] = []
    for m in _REF_IN_TEXT_RE.finditer(text):
        p1, c1, l1, p2, c2, l2 = m.groups()
        try:
            start = BekkerRef(int(p1), c1.lower(), int(l1) if l1 else None)
            s = start.to_int() if start.line is not None else column_start(start.page, start.column)
            if p2:
                end = BekkerRef(int(p2), c2.lower(), int(l2) if l2 else None)
                e = end.to_int() if end.line is not None else column_end(end.page, end.column)
            elif l2 and start.line is not None:
                e = encode(start.page, start.column, int(l2))
            elif start.line is None:
                e = column_end(start.page, start.column)
            else:
                e = s
        except BekkerError:
            continue
        if e >= s:
            out.append((s, e))
    return out


def format_range(start: int | None, end: int | None, *, approximate: bool = False) -> str:
    """Human form: ``1106b36–1107a8`` or ``1106b36–40``; adds "(aprox.)" if asked."""
    if start is None:
        return ""
    a = decode(start)
    if end is None or end == start:
        out = str(a)
    else:
        b = decode(end)
        if (a.page, a.column) == (b.page, b.column) and b.line is not None:
            out = f"{a}–{b.line}"
        else:
            out = f"{a}–{b}"
    return f"{out} (aprox.)" if approximate else out


def overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Number of encoded positions shared by two inclusive ranges (0 if disjoint).

    Positions are encoded lines, so for ranges inside one column this is a line count.
    Across a column break the count includes the unused encoding gap; use it only to
    test for overlap or compare overlaps of similar ranges.
    """
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return max(0, hi - lo + 1)


def line_distance(a: int, b: int) -> int:
    """Approximate distance in Bekker lines between two encoded refs.

    Columns are treated as ~40 lines long (typical Bekker column), ignoring the
    encoding gap between columns.
    """
    ra, rb = decode(min(a, b)), decode(max(a, b))
    cols_a = ra.page * 2 + (1 if ra.column == "b" else 0)
    cols_b = rb.page * 2 + (1 if rb.column == "b" else 0)
    la = ra.line or 0
    lb = rb.line or 0
    return (cols_b - cols_a) * 40 + (lb - la)
