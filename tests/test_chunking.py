import re

import pytest

from app import bekker
from app.chunking import (
    ChunkTooLongError,
    chunk_unit,
    chunk_units,
    chunking_config,
    estimate_tokens,
)
from app.models import BekkerPrecision, Unit, Work
from app.settings import Settings

WORK = Work(
    work_id="eth-nic",
    abbr="EN",
    title_es="Ética a Nicómaco",
    title_en="Nicomachean Ethics",
    title_la="Ethica Nicomachea",
    authenticity="genuine",
    structure="book_chapter",
)


def settings(**overrides) -> Settings:
    values = {
        "chunk_min_words": 150,
        "chunk_max_words": 300,
        "chunk_overlap_words": 0,
        "chunk_max_tokens": 4000,  # word limits bind; token tests lower it
        "chars_per_token": 3.0,
        "bekker_column_lines": 35,
    }
    values.update(overrides)
    return Settings(**values)


def paragraph(tag: str, n_words: int, sentence_every: int | None = None) -> str:
    """n_words synthetic words; a full stop every `sentence_every` words if given."""
    words = []
    for i in range(n_words):
        word = f"{tag}{i}"
        if sentence_every and (i + 1) % sentence_every == 0:
            word += "."
        words.append(word)
    return " ".join(words)


def unit(
    text: str,
    *,
    unit_id: str = "eth-nic.en.test:2.6",
    lang: str = "en",
    start: str | None = None,
    end: str | None = None,
    anchors: list[tuple[int, str]] | None = None,
    precision: BekkerPrecision = BekkerPrecision.interpolated,
    seq: int = 1,
) -> Unit:
    return Unit(
        unit_id=unit_id,
        work_id="eth-nic",
        edition_id=unit_id.split(":")[0],
        lang=lang,
        divisions=[("book", "2"), ("chapter", "6")],
        seq=seq,
        bekker_start=bekker.parse(start).to_int() if start else None,
        bekker_end=bekker.parse(end).to_int() if end else None,
        bekker_precision=precision if start else BekkerPrecision.none,
        bekker_anchors=[(off, bekker.parse(ref).to_int()) for off, ref in (anchors or [])],
        text=text,
    )


def word_offset(text: str, index: int) -> int:
    """Character offset of the index-th word of text."""
    return [m.start() for m in re.finditer(r"\S+", text)][index]


def test_cuts_at_paragraph_boundaries():
    paragraphs = [paragraph(f"p{i}w", 100) for i in range(5)]
    u = unit("\n\n".join(paragraphs), start="1106b1", end="1106b40", anchors=[(0, "1106b1")])
    chunks = chunk_unit(u, WORK, settings())
    assert [len(c.text.split()) for c in chunks] == [300, 200]
    for c in chunks:
        assert all(p in paragraphs for p in c.text.split("\n\n"))  # whole paragraphs only


def test_single_newlines_are_paragraphs_when_there_are_no_blank_lines():
    text = "\n".join(paragraph(f"p{i}w", 100) for i in range(4))
    chunks = chunk_unit(unit(text), WORK, settings())
    assert [len(c.text.split()) for c in chunks] == [200, 200]


def test_long_paragraph_falls_back_to_sentences():
    text = paragraph("w", 700, sentence_every=10)
    chunks = chunk_unit(unit(text), WORK, settings())
    assert len(chunks) == 3
    for c in chunks:
        assert c.text.endswith(".")
        assert 150 <= len(c.text.split()) <= 300


def test_without_any_boundary_cuts_between_words():
    text = paragraph("w", 700)
    chunks = chunk_unit(unit(text), WORK, settings())
    assert all(len(c.text.split()) <= 300 for c in chunks)
    assert " ".join(c.text for c in chunks) == text


def test_short_tail_is_balanced():
    # 5 paragraphs of 70 words: greedy would give 280 + 70; balanced gives 210 + 140.
    text = "\n\n".join(paragraph(f"p{i}w", 70) for i in range(5))
    chunks = chunk_unit(unit(text), WORK, settings())
    assert [len(c.text.split()) for c in chunks] == [210, 140]


def test_every_chunk_reaches_the_minimum_when_paragraphs_allow_it():
    # Whole paragraphs can make 150 + 183 + 192; cutting greedily and then fixing only the
    # tail left 188 + 197 + 140.
    sizes = [41, 43, 66, 38, 54, 43, 48, 52, 31, 49, 60]
    text = "\n\n".join(paragraph(f"p{i}w", n) for i, n in enumerate(sizes))
    chunks = chunk_unit(unit(text), WORK, settings(chunk_max_words=200))
    assert [len(c.text.split()) for c in chunks] == [150, 183, 192]


def test_sentences_are_used_when_paragraphs_leave_a_short_chunk():
    # Paragraphs of 200, 200 and 60 words need three chunks, one of 60; from sentences
    # (20 words each) two chunks of 240 and 220 fit.
    text = "\n\n".join(paragraph(f"p{i}w", n, 20) for i, n in enumerate([200, 200, 60]))
    chunks = chunk_unit(unit(text), WORK, settings(chunk_max_words=250))
    assert sorted(len(c.text.split()) for c in chunks) == [220, 240]
    assert all(c.text.endswith(".") for c in chunks)
    assert " ".join(" ".join(c.text.split()) for c in chunks) == " ".join(text.split())


def test_never_crosses_units_and_numbers_chunks_per_edition():
    u1 = unit(paragraph("a", 400, 20), unit_id="ed.en:1.1", seq=1)
    u2 = unit(paragraph("b", 100, 20), unit_id="ed.en:1.2", seq=2)
    chunks = chunk_units([u2, u1], {"eth-nic": WORK}, settings())
    assert [c.chunk_id for c in chunks] == ["ed.en:1.1#1", "ed.en:1.1#2", "ed.en:1.2#1"]
    assert [c.seq for c in chunks] == [0, 1, 2]
    for c in chunks:
        assert len({word[0] for word in c.text.split()}) == 1  # words of one unit only


def test_bekker_interpolated_by_word_count():
    # 4 paragraphs of 50 words; markers at word 0 (1106a1), 100 (1106a21) and 150 (1106a31).
    text = "\n\n".join(paragraph(f"p{i}w", 50) for i in range(4))
    anchors = [
        (0, "1106a1"),
        (word_offset(text, 100), "1106a21"),
        (word_offset(text, 150), "1106a31"),
    ]
    u = unit(text, start="1106a1", end="1106a39", anchors=anchors)
    chunks = chunk_unit(u, WORK, settings(chunk_min_words=25, chunk_max_words=50))
    ranges = [bekker.format_range(c.bekker_start, c.bekker_end) for c in chunks]
    # word 50 lies halfway between 1106a1 and 1106a21 -> line 11, shared by both chunks; a
    # chunk that stops right before a marker ends on the line before it (21 -> 20, 31 -> 30);
    # word 200 is the unit end.
    assert ranges == ["1106a1–11", "1106a11–20", "1106a21–30", "1106a31–39"]
    precisions = [c.bekker_precision for c in chunks]
    assert precisions == [
        BekkerPrecision.interpolated,  # end interpolated
        BekkerPrecision.interpolated,  # start interpolated
        BekkerPrecision.line,  # starts on a marker, ends on the line before the next one
        BekkerPrecision.interpolated,  # end is the unit boundary, not a marker
    ]


def test_chunk_before_a_column_marker_ends_on_the_previous_column():
    # The next chunk starts at the marker 1107a1: this one ends on the last line of 1106b,
    # whose length is assumed (35 lines, or the highest line marked in the unit).
    text = "\n\n".join(paragraph(f"p{i}w", 50) for i in range(2))
    anchors = [(0, "1106b20"), (word_offset(text, 50), "1107a1")]
    u = unit(text, start="1106b20", end="1107a20", anchors=anchors)
    first, second = chunk_unit(u, WORK, settings(chunk_min_words=25, chunk_max_words=50))
    assert bekker.format_range(first.bekker_start, first.bekker_end) == "1106b20–35"
    assert first.bekker_precision == BekkerPrecision.interpolated  # the column length is assumed
    assert bekker.format_int(second.bekker_start) == "1107a1"


def test_bekker_interpolation_across_a_column_break():
    # 1106a30 at word 0 and 1106b5 at word 100: with 35-line columns that is 10 lines, so
    # word 50 falls on the last line of column a.
    text = "\n\n".join(paragraph(f"p{i}w", 50) for i in range(3))
    anchors = [(0, "1106a30"), (word_offset(text, 100), "1106b5")]
    u = unit(text, start="1106a30", end="1106b20", anchors=anchors)
    chunks = chunk_unit(u, WORK, settings(chunk_min_words=25, chunk_max_words=50))
    assert [bekker.format_int(c.bekker_end) for c in chunks] == ["1106a35", "1106b4", "1106b20"]
    assert bekker.format_int(chunks[1].bekker_start) == "1106a35"


def test_ranges_clamped_to_unit_and_monotonic():
    text = "\n\n".join(paragraph(f"p{i}w", 60) for i in range(5))
    # A marker before the unit start and one after its end (bad source data) are clamped.
    anchors = [(0, "1106a1"), (word_offset(text, 120), "1106b30")]
    u = unit(text, start="1106a5", end="1106a30", anchors=anchors)
    chunks = chunk_unit(u, WORK, settings(chunk_min_words=50, chunk_max_words=120))
    for c in chunks:
        assert u.bekker_start <= c.bekker_start <= c.bekker_end <= u.bekker_end
    starts = [c.bekker_start for c in chunks]
    assert starts == sorted(starts)


def test_chapter_level_range_is_not_interpolated():
    text = "\n\n".join(paragraph(f"p{i}w", 100) for i in range(4))
    u = unit(text, start="1106a1", end="1107a8", precision=BekkerPrecision.chapter)
    chunks = chunk_unit(u, WORK, settings())
    assert len(chunks) == 2
    for c in chunks:
        assert (c.bekker_start, c.bekker_end) == (u.bekker_start, u.bekker_end)
        assert c.bekker_precision == BekkerPrecision.chapter


def test_no_bekker():
    u = unit(paragraph("w", 50))
    [c] = chunk_unit(u, WORK, settings())
    assert (c.bekker_start, c.bekker_end, c.bekker_precision) == (None, None, BekkerPrecision.none)
    assert c.embed_text.split("\n")[0] == "Aristotle, Nicomachean Ethics, book II, ch. 6"


def test_header_in_the_edition_language():
    u = unit(paragraph("w", 50), lang="es", start="1106b36", end="1107a8")
    [c] = chunk_unit(u, WORK, settings())
    assert c.embed_text.split("\n")[0] == (
        "Aristóteles, Ética a Nicómaco, libro II, cap. 6, 1106b36–1107a8"
    )
    assert c.embed_text.endswith(c.text)
    u_en = unit(paragraph("w", 50), lang="en", start="1106b36", end="1107a8")
    [c_en] = chunk_unit(u_en, WORK, settings())
    assert c_en.embed_text.startswith("Aristotle, Nicomachean Ethics, book II, ch. 6, 1106b36–")


def test_token_budget_limits_chunk_size():
    text = "\n\n".join(paragraph(f"p{i}w", 20) for i in range(20))
    s = settings(chunk_min_words=10, chunk_max_words=300, chunk_max_tokens=120)
    chunks = chunk_unit(unit(text, start="1106a1", end="1106b30"), WORK, s)
    assert len(chunks) > 1
    assert all(estimate_tokens(c.embed_text, s) <= 120 for c in chunks)


def test_chunk_over_token_limit_is_a_hard_failure():
    # Overlap is added after packing, so a large overlap can push a chunk over the limit.
    text = "\n\n".join(paragraph(f"p{i}w", 20) for i in range(20))
    s = settings(
        chunk_min_words=10, chunk_max_words=300, chunk_max_tokens=120, chunk_overlap_words=40
    )
    with pytest.raises(ChunkTooLongError):
        chunk_unit(unit(text), WORK, s)


def test_header_that_leaves_no_room_fails():
    with pytest.raises(ChunkTooLongError):
        chunk_unit(unit(paragraph("w", 10)), WORK, settings(chunk_max_tokens=10))


def test_overlap_repeats_the_last_words():
    text = "\n\n".join(paragraph(f"p{i}w", 100) for i in range(4))
    chunks = chunk_unit(unit(text), WORK, settings(chunk_max_words=200, chunk_overlap_words=5))
    first, second = chunks[0].text.split(), chunks[1].text.split()
    assert second[:5] == first[-5:]


def test_deterministic_and_stable_ids():
    text = "\n\n".join(paragraph(f"p{i}w", 90, 15) for i in range(7))
    u = unit(text, start="1106a1", end="1107a20", anchors=[(0, "1106a1")])
    a = chunk_unit(u, WORK, settings())
    b = chunk_unit(u, WORK, settings())
    assert a == b
    assert [c.chunk_id for c in a] == [f"{u.unit_id}#{n}" for n in range(1, len(a) + 1)]


def test_chunking_config_tracks_settings():
    assert chunking_config(settings()) != chunking_config(settings(chunk_max_words=250))
    assert chunking_config(settings())["max_words"] == 300
