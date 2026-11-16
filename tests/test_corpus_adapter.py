"""Tests for the Perseus TEI adapter (app/corpus/adapters/perseus_tei.py). No network.

Real text: one chapter of the Nicomachean Ethics (tests/fixtures/perseus). The four Bekker
encodings and the chapter-range derivation use tiny synthetic TEI written here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import bekker
from app.corpus.adapters.perseus_tei import (
    LINE,
    MARK,
    PARA,
    SOFT,
    _finalize,
    adapt,
    parse_tei,
    position_at,
    unit_spans,
)
from app.models import BekkerPrecision, Edition, Work
from app.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "perseus"
NE = FIXTURES / "ne_7_11_eng.xml"
NE_BARE = FIXTURES / "ne_7_11_eng_nomarkers.xml"
SETTINGS = Settings(bekker_column_lines=35)


def enc(ref: str) -> int:
    return bekker.parse(ref).to_int()


def tei(body: str, *, lang: str = "eng") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
<teiHeader><fileDesc>
<titleStmt><title>Synthetic</title></titleStmt>
<publicationStmt><availability>
<licence target="https://creativecommons.org/licenses/by-sa/4.0/">Synthetic.</licence>
</availability></publicationStmt>
<sourceDesc><biblStruct><monogr><title>Synthetic</title>
<editor role="translator">Test Translator</editor><imprint><date>1926</date></imprint>
</monogr></biblStruct></sourceDesc>
</fileDesc></teiHeader>
<text><body><div type="translation" xml:lang="{lang}">{body}</div></body></text>
</TEI>
"""


def write(tmp_path: Path, name: str, body: str, **kw: str) -> Path:
    path = tmp_path / name
    path.write_text(tei(body, **kw), encoding="utf-8")
    return path


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def make_work(structure: str = "book_chapter") -> Work:
    return Work(
        work_id="eth-nic",
        abbr="EN",
        title_es="Ética a Nicómaco",
        title_en="Nicomachean Ethics",
        title_la="Ethica Nicomachea",
        authenticity="genuine",
        structure=structure,
        bekker_start="1094a",
        bekker_end="1181b",
    )


def make_edition(lang: str = "en", has_bekker: bool = True) -> Edition:
    return Edition(
        edition_id=f"eth-nic.{lang}.test",
        work_id="eth-nic",
        lang=lang,
        translator="Harris Rackham" if lang == "en" else None,
        url="https://example.org/x.xml",
        pinned_version="x",
        licence="CC BY-SA 4.0",
        licence_evidence="x",
        attribution="x",
        adapter="perseus_tei",
        tier=0,
        has_bekker=has_bekker,
        status="indexed",
    )


# --- the NE fixture --------------------------------------------------------------------


def test_fixture_reads_division_types_from_the_tei():
    parsed = parse_tei(NE, unit_depth=2)
    assert parsed.level_types == ["book", "section", "subsection"]
    assert [u.path for u in parsed.units] == [("7", "11")]
    assert parsed.warnings == []


def test_fixture_anchors_point_at_the_marked_words():
    unit = parse_tei(NE, unit_depth=2).units[0]
    assert [pos for _, pos in unit.anchors] == [enc("1152b1"), enc("1152b20")]
    (first, _), (second, _) = unit.anchors
    assert first == 0 and unit.text.startswith("It is also the business")
    assert unit.text[second:].startswith("and animals pursue pleasures.")


def test_fixture_notes_are_moved_out_of_the_text():
    unit = parse_tei(NE, unit_depth=2).units[0]
    assert unit.notes[:2] == ["2.3.1.", "μακάριος from μάλα χαίρειν: cf. 5.4.9."]
    assert unit.notes[2].startswith("Of these three views, the first is that of Speusippus")
    assert "Speusippus" not in unit.text and "2.3.1" not in unit.text
    assert "we have established that Moral Virtue" in unit.text


def test_fixture_text_is_clean():
    text = parse_tei(NE, unit_depth=2).units[0].text
    assert "‘supreme bliss’" in text and "‘to enjoy.’" in text
    assert "<" not in text and "&" not in text and "  " not in text
    assert "Book 7" not in text  # <head> dropped
    paragraphs = text.split("\n\n")
    assert paragraphs[0].startswith("It is also the business")
    assert paragraphs[-1] == "These then, more or less, are the current views."
    assert "\n(b) The temperate man avoids pleasures." in text  # <lb/> keeps the list


def test_markers_do_not_change_the_text():
    marked = parse_tei(NE, unit_depth=2).units[0]
    bare = parse_tei(NE_BARE, unit_depth=2).units[0]
    assert marked.text == bare.text
    assert marked.notes == bare.notes
    assert bare.anchors == []


def test_fixture_units_without_reference():
    result = adapt(NE, make_edition(), make_work(), settings=SETTINGS)
    (unit,) = result.units
    assert unit.unit_id == "eth-nic.en.test:7.11"
    assert unit.divisions == [("book", "7"), ("chapter", "11")]
    assert unit.seq == 1
    assert unit.bekker_start == enc("1152b1")  # a marker sits on the first word
    assert enc("1152b20") < unit.bekker_end <= bekker.encode(1152, "b", 35)
    assert unit.bekker_precision == BekkerPrecision.interpolated  # end extrapolated
    assert unit.bekker_anchors == [(0, enc("1152b1")), (1595, enc("1152b20"))]
    assert result.header["licence_target"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert result.header["editor"] == "Harris Rackham"
    assert result.header["date"] == "1926"


def test_stripped_fixture_has_no_bekker():
    (unit,) = adapt(NE_BARE, make_edition(), make_work(), settings=SETTINGS).units
    assert unit.bekker_start is None and unit.bekker_end is None
    assert unit.bekker_precision == BekkerPrecision.none
    assert unit.bekker_anchors == []


def test_edition_without_bekker_drops_ranges():
    (unit,) = adapt(NE, make_edition(has_bekker=False), make_work(), settings=SETTINGS).units
    assert unit.bekker_start is None and unit.bekker_anchors == []


# --- the four Bekker encodings ------------------------------------------------------------


def test_page_milestones_and_running_position(tmp_path):
    path = write(
        tmp_path,
        "page.xml",
        """
<div type="textpart" subtype="book" n="1"><head>Book 1</head>
 <div type="textpart" subtype="section" n="1">
  <div type="textpart" subtype="subsection" n="1"><p><milestone unit="page" resp="Bekker"
   n="1094a"/><milestone unit="line" resp="Bekker" n="1"/>alpha beta</p></div>
  <div type="textpart" subtype="subsection" n="2"><p>gamma <milestone unit="line"
   resp="Bekker" n="20"/>delta</p></div>
 </div>
 <div type="textpart" subtype="section" n="2"><p>epsilon zeta</p>
  <milestone unit="page" resp="Bekker" n="1094b"/><milestone unit="line" resp="Bekker" n="1"/>
 </div>
 <div type="textpart" subtype="section" n="3"><p>eta theta</p></div>
</div>""",
    )
    parsed = parse_tei(path, unit_depth=2)
    a, b, c = parsed.units
    assert a.text == "alpha beta\n\ngamma delta"
    assert a.anchors == [(0, enc("1094a1")), (a.text.index("delta"), enc("1094a20"))]
    assert b.anchors == []  # the markers after its last word mark the next unit's start
    assert c.anchors == [(0, enc("1094b1"))]
    spans = unit_spans(parsed, SETTINGS)
    assert spans[("1", "3")].start == enc("1094b1") and spans[("1", "3")].exact_start
    assert spans[("1", "2")].start > enc("1094a20")  # carried and interpolated
    assert not spans[("1", "2")].exact_start


def test_section_milestones_as_pages_eudemian_ethics(tmp_path):
    path = write(
        tmp_path,
        "ee.xml",
        """
<div type="textpart" subtype="book" n="1"><div type="textpart" subtype="section" n="1">
 <p><milestone unit="section" n="1214a"/><milestone unit="line" resp="Bekker" n="1"/>one two
 <milestone unit="section" resp="Rackham" n="loeb_section_2"/>three
 <milestone unit="line" resp="Bekker" n="20"/>four</p></div></div>
<div type="textpart" subtype="book" n="2"><div type="textpart" subtype="section" n="1">
 <p><milestone unit="section" n="1214a"/><milestone unit="line" resp="Bekker" n="25"/>five
 <milestone unit="section" n="1214b"/><milestone unit="line" resp="Bekker" n="1"/>six</p>
</div></div>""",
    )
    first, second = parse_tei(path, unit_depth=2).units
    assert first.text == "one two three four"
    assert [p for _, p in first.anchors] == [enc("1214a1"), enc("1214a20")]
    # The page repeated at the book boundary does not reset the line to 1.
    assert second.anchors == [(0, enc("1214a25")), (second.text.index("six"), enc("1214b1"))]


def test_bekker_page_divisions_politics(tmp_path):
    path = write(
        tmp_path,
        "pol.xml",
        """
<div type="textpart" subtype="book" n="1">
 <div type="textpart" subtype="bekker_page" resp="perseus" n="1252a"><p><milestone
  unit="page" resp="Bekker" n="1252a"/><milestone n="1" ed="Bekker" unit="line"/><milestone
  unit="section" resp="Rackham" n="loeb_section_1"/>alpha beta <milestone n="20"
  ed="Bekker" unit="line"/>gamma <milestone unit="page" resp="Bekker" n="1252b"/><milestone
  n="1" ed="Bekker" unit="line"/>delta</p></div>
 <div type="textpart" subtype="bekker_page" resp="perseus" n="1252b"><p>epsilon</p></div>
 <div type="textpart" subtype="bekker_page" resp="perseus" n="1253a"><p>zeta</p></div>
 <div type="textpart" subtype="bekker_page" resp="perseus" n="1253b"><p>eta <milestone
  n="1253b" unit="section"/><milestone n="1" ed="Bekker" unit="line"/>theta</p></div>
</div>""",
    )
    parsed = parse_tei(path, unit_depth=2)
    assert parsed.level_types == ["book", "bekker_page"]
    p1, p2, p3, p4 = parsed.units
    assert [p for _, p in p1.anchors] == [enc("1252a1"), enc("1252a20"), enc("1252b1")]
    assert p2.anchors == []  # its page began in the previous division: not counted twice
    assert p3.anchors == [(0, enc("1253a1"))]  # a division alone marks its page
    # The division holds its own page milestone: the milestone is the precise point.
    assert p4.anchors == [(p4.text.index("theta"), enc("1253b1"))]
    result = adapt(path, make_edition(), make_work("book_bekker_page"), settings=SETTINGS)
    assert result.units[0].divisions == [("book", "1"), ("bekker_page", "1252a")]
    assert result.units[0].unit_id == "eth-nic.en.test:1.1252a"


def test_work_not_starting_at_line_one(tmp_path):
    path = write(
        tmp_path,
        "metaph.xml",
        """
<div type="textpart" subtype="chapter" n="1">
 <div type="textpart" subtype="subchapter" n="1"><p><milestone unit="page" resp="Bekker"
  n="980a"/> <milestone resp="Bekker" unit="Line" n="21"/>alpha beta</p></div>
 <div type="textpart" subtype="subchapter" n="2"><p>gamma <milestone unit="line"
  resp="Bekker" n="25"/>delta</p></div>
</div>
<div type="textpart" subtype="chapter" n="2"><p>epsilon</p></div>""",
    )
    parsed = parse_tei(path, unit_depth=1)
    assert [u.path for u in parsed.units] == [("1",), ("2",)]
    first = parsed.units[0]
    assert first.anchors == [(0, enc("980a21")), (first.text.index("delta"), enc("980a25"))]
    assert parsed.markers[0] == (0, enc("980a21"))  # never 980a1


def test_unknown_milestones_and_line_before_page_are_ignored(tmp_path):
    path = write(
        tmp_path,
        "odd.xml",
        """
<div type="textpart" subtype="chapter" n="1"><p><milestone unit="line" resp="Bekker"
 n="7"/>alpha <milestone unit="card" n="3"/><milestone unit="line" n="9"/>beta</p></div>""",
    )
    parsed = parse_tei(path, unit_depth=1)
    assert parsed.units[0].anchors == []
    assert any("antes de toda página" in w for w in parsed.warnings)


# --- cleaning --------------------------------------------------------------------------------


def test_inline_elements_are_cleaned(tmp_path):
    path = write(
        tmp_path,
        "clean.xml",
        """
<div type="textpart" subtype="chapter" n="1">
 <p>the &lt;supplied&gt; words, a <gap reason="lost"/> gap and <choice><sic>teh</sic>
 <corr>the</corr></choice> end; the first<note resp="T">A note.</note>2 units<note
 resp="T">Second <q rend="double">quoted</q> <choice><sic>noet</sic>
 <corr>note</corr></choice>.</note>
 , then <sic>a</sic> self-
 restraint word, said in the<title>Ethics</title>, replied<q rend="double">Yes</q>then.<cit>
 <quote type="verse"><l>A verse line,</l><l>another line.</l></quote>
 <bibl n="Hom. Il. 1.1">Hom. Il. 1.1</bibl></cit></p>
</div>""",
    )
    unit = parse_tei(path, unit_depth=1).units[0]
    assert unit.text == (
        "the ⟨supplied⟩ words, a … gap and the end; the first 2 units, then a "
        "self-restraint word, said in the Ethics, replied “Yes” then.\n\nA verse line,\n"
        "another line."
    )
    assert unit.notes == ["A note.", "Second “quoted” note.", "Hom. Il. 1.1"]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("a  b\t c", "a b c"),
        ("word , and ; so", "word, and; so"),
        ("“ Socrates” said", "“Socrates” said"),
        ("the end . Then", "the end. Then"),
        ("here . . . there", "here . . . there"),
        ("self-\n   restraint", "self-restraint"),
        ("self- restraint", "self- restraint"),  # no source line break: left alone
        (f"in the{SOFT}Ethics{SOFT}, and", "in the Ethics, and"),
        (f"replied{SOFT}“His master”{SOFT}they", "replied “His master” they"),
        (f"Let{SOFT} us ({SOFT}“so”{SOFT})", "Let us (“so”)"),
        ("villages( κῶμαι ) here", "villages(κῶμαι) here"),
        (f"the surname of{SOFT}{MARK}Ausonians", "the surname of Ausonians"),
        (f"{PARA}one{PARA}{PARA}two{LINE}three", "one\n\ntwo\nthree"),
    ],
)
def test_finalize_whitespace(raw, clean):
    assert _finalize(raw)[0] == clean


def test_finalize_marker_offsets_point_at_next_word():
    text, offsets = _finalize(f"alpha {MARK} beta{MARK}{PARA}gamma{MARK}")
    assert text == "alpha beta\n\ngamma"
    assert offsets == [6, 12, len(text)]


# --- interpolation and chapter ranges from the Greek ----------------------------------------


def test_position_at_interpolates_between_markers():
    markers = [(0, enc("100a1")), (10, enc("100a6"))]
    kw = {"column_lines": 35, "words_per_line": None}
    assert position_at(markers, 0, **kw) == (enc("100a1"), True)
    assert position_at(markers, 5, **kw) == (enc("100a3"), False)
    assert position_at(markers, 10, **kw) == (enc("100a6"), True)
    assert position_at([(5, enc("100a1"))], 2, **kw) == (None, False)


def test_position_at_across_a_column_break_and_after_the_last_marker():
    markers = [(0, enc("100a30")), (10, enc("100b1"))]
    assert position_at(markers, 5, column_lines=35, words_per_line=None) == (
        enc("100a33"),
        False,
    )
    tail = [(0, enc("100a1")), (10, enc("100a6"))]
    assert position_at(tail, 20, column_lines=35, words_per_line=2.0) == (enc("100a11"), False)


GREEK = f"""
<div type="textpart" subtype="book" n="1">
 <div type="textpart" subtype="section" n="1"><p><milestone unit="page" resp="Bekker"
  n="10a"/><milestone unit="line" resp="Bekker" n="1"/>{words(20, "g")} <milestone
  unit="line" resp="Bekker" n="5"/>{words(20, "h")} <milestone unit="line" resp="Bekker"
  n="10"/>{words(10, "i")}</p></div>
 <div type="textpart" subtype="section" n="2"><p>{words(10, "j")} <milestone unit="line"
  resp="Bekker" n="15"/>{words(20, "k")}</p></div>
 <div type="textpart" subtype="section" n="3"><p><milestone unit="line" resp="Bekker"
  n="20"/>{words(20, "l")} <milestone unit="page" resp="Bekker" n="10b"/><milestone
  unit="line" resp="Bekker" n="1"/>{words(20, "m")}</p></div>
</div>
<div type="textpart" subtype="book" n="2">
 <div type="textpart" subtype="section" n="1"><p><milestone unit="line" resp="Bekker"
  n="5"/>{words(20, "n")}</p></div>
 <div type="textpart" subtype="section" n="2"><p><milestone unit="line" resp="Bekker"
  n="10"/>{words(15, "o")}</p></div>
</div>"""

# Same divisions; sparse markers. In 1.2 the line-20 marker falls after the Greek end of
# 1.2 (10a19): the translation puts the 1.2|1.3 boundary later than the Greek does.
ENGLISH = f"""
<div type="textpart" subtype="book" n="1">
 <div type="textpart" subtype="section" n="1"><p><milestone unit="page" resp="Bekker"
  n="10a"/><milestone unit="line" resp="Bekker" n="1"/>{words(30, "a")}</p></div>
 <div type="textpart" subtype="section" n="2"><p>{words(20, "b")} <milestone unit="line"
  resp="Bekker" n="20"/>{words(10, "c")}</p></div>
 <div type="textpart" subtype="section" n="3"><p>{words(30, "d")} <milestone unit="page"
  resp="Bekker" n="10b"/><milestone unit="line" resp="Bekker" n="1"/>{words(15, "e")}</p>
 </div>
</div>
<div type="textpart" subtype="book" n="2">
 <div type="textpart" subtype="section" n="1"><p><milestone unit="line" resp="Bekker"
  n="5"/>{words(20, "f")}</p></div>
 <div type="textpart" subtype="section" n="2"><p>{words(10, "x")}</p></div>
 <div type="textpart" subtype="section" n="9"><p>{words(5, "y")}</p></div>
</div>"""


def test_chapter_ranges_from_the_greek(tmp_path):
    spans = unit_spans(parse_tei(write(tmp_path, "g.xml", GREEK), unit_depth=2), SETTINGS)
    s = {k: (bekker.format_int(v.start), bekker.format_int(v.end)) for k, v in spans.items()}
    assert s[("1", "1")] == ("10a1", "10a12")  # end interpolated between lines 10 and 15
    assert s[("1", "2")] == ("10a12", "10a19")  # 1.3 starts on line 20: 1.2 ends on 19
    assert s[("1", "3")] == ("10a20", "10b4")  # last of its book: interpolated end
    assert s[("2", "1")] == ("10b5", "10b9")
    exact = {k: (v.exact_start, v.exact_end) for k, v in spans.items()}
    assert exact[("1", "1")] == (True, False)
    assert exact[("1", "2")] == (False, True)
    assert exact[("2", "1")] == (True, True)


def test_translation_takes_greek_ranges_and_keeps_its_anchors(tmp_path):
    greek = write(tmp_path, "g.xml", GREEK, lang="grc")
    english = write(tmp_path, "e.xml", ENGLISH)
    result = adapt(english, make_edition(), make_work(), greek, settings=SETTINGS)
    units = {u.unit_id.split(":")[1]: u for u in result.units}

    u11 = units["1.1"]
    assert (u11.bekker_start, u11.bekker_end) == (enc("10a1"), enc("10a12"))
    assert u11.bekker_precision == BekkerPrecision.interpolated
    assert u11.bekker_anchors == [(0, enc("10a1"))]

    u21 = units["2.1"]
    assert (u21.bekker_start, u21.bekker_end) == (enc("10b5"), enc("10b9"))
    assert u21.bekker_precision == BekkerPrecision.line

    # Boundary 1.2|1.3 is taken from the translation's markers (interpolated).
    u12, u13 = units["1.2"], units["1.3"]
    assert u12.bekker_start == enc("10a12")
    assert u12.bekker_end == enc("10a23") and u13.bekker_start == enc("10a24")
    assert u13.bekker_end == enc("10b4")
    assert any(w.startswith("límite 1.2|1.3") for w in result.warnings)
    for u in result.units:
        assert all(u.bekker_start <= pos <= u.bekker_end for _, pos in u.bekker_anchors)

    # 2.9 has no Greek counterpart: its range comes from the translation itself.
    assert units["2.9"].bekker_start is not None
    assert any("sin equivalente en el griego" in w and "2.9" in w for w in result.warnings)

    starts = [u.bekker_start for u in result.units]
    ends = [u.bekker_end for u in result.units]
    assert starts == sorted(starts) and ends == sorted(ends)
