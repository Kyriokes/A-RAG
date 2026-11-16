import pytest

from app import bekker
from app.citations import CitationError, build_citation, load_catalogue, roman
from app.models import Chunk, Edition, Work

WORKS = {
    "eth-nic": Work(
        work_id="eth-nic",
        abbr="EN",
        title_es="Ética a Nicómaco",
        title_en="Nicomachean Ethics",
        title_la="Ethica Nicomachea",
        authenticity="genuine",
        structure="book_chapter",
    ),
    "pol": Work(
        work_id="pol",
        abbr="Pol.",
        title_es="Política",
        title_en="Politics",
        title_la="Politica",
        authenticity="genuine",
        structure="book_bekker_page",
    ),
    "poet": Work(
        work_id="poet",
        abbr="Poet.",
        title_es="Poética",
        title_en="Poetics",
        title_la="Poetica",
        authenticity="genuine",
        structure="chapter",
    ),
    "oec": Work(
        work_id="oec",
        abbr="Oec.",
        title_es="Económicos",
        title_en="Economics",
        title_la="Oeconomica",
        authenticity="disputed",
        structure="book_chapter",
    ),
}


def edition(edition_id: str, translator: str | None = "Test Translator") -> Edition:
    return Edition(
        edition_id=edition_id,
        work_id=edition_id.split(".")[0],
        lang=edition_id.split(".")[1],
        translator=translator,
        url=f"https://example.org/{edition_id}.xml",
        pinned_version="test",
        licence="CC BY-SA 4.0",
        licence_evidence="test",
        attribution="test",
        adapter="test",
        tier=0,
        has_bekker=translator != "Azcárate",
        status="indexed",
    )


EDITIONS = {
    e.edition_id: e
    for e in [
        edition("eth-nic.en.test"),
        edition("eth-nic.es.azcarate", translator="Azcárate"),
        edition("pol.en.test"),
        edition("poet.en.test"),
        edition("oec.en.test"),
    ]
}


def chunk(
    edition_id: str,
    divisions: list[tuple[str, str]],
    span: tuple[int, int] | str | None,
    precision: str,
    text: str = "Texto del fragmento.",
) -> Chunk:
    if isinstance(span, str):
        span = bekker.parse_range(span)
    start, end = span if span else (None, None)
    return Chunk(
        chunk_id=f"{edition_id}:x#1",
        unit_id=f"{edition_id}:x",
        work_id=edition_id.split(".")[0],
        edition_id=edition_id,
        lang=edition_id.split(".")[1],
        divisions=divisions,
        seq=1,
        bekker_start=start,
        bekker_end=end,
        bekker_precision=precision,
        text=text,
        embed_text=f"cabecera\n{text}",
    )


NE_2_6 = [("book", "2"), ("chapter", "6")]


def locator(c: Chunk, lang: str = "es") -> str:
    return build_citation(1, c, WORKS, EDITIONS, lang=lang).locator


def test_interpolated_range_is_approximate():
    c = chunk("eth-nic.en.test", NE_2_6, "1106b36-1107a8", "interpolated")
    assert locator(c) == "EN II.6, 1106b36–1107a8 (aprox.)"
    assert locator(c, "en") == "EN II.6, 1106b36–1107a8 (approx.)"


def test_line_precision_is_exact_and_same_column_is_short():
    assert locator(chunk("eth-nic.en.test", NE_2_6, "1106b36-1107a8", "line")) == (
        "EN II.6, 1106b36–1107a8"
    )
    assert locator(chunk("eth-nic.en.test", NE_2_6, "1106b10-1106b30", "interpolated")) == (
        "EN II.6, 1106b10–30 (aprox.)"
    )
    assert locator(chunk("eth-nic.en.test", NE_2_6, "1106b10-1106b10", "line")) == (
        "EN II.6, 1106b10"
    )


def test_page_precision_gives_columns_and_skips_the_bekker_page_level():
    divisions = [("book", "1"), ("bekker_page", "1253a")]
    c = chunk("pol.en.test", divisions, "1253a1-1253b20", "page")
    assert locator(c) == "Pol. I, 1253a–1253b"
    same = chunk("pol.en.test", divisions, "1253a1-1253a30", "page")
    assert locator(same) == "Pol. I, 1253a"


def test_column_level_bounds_never_show_sentinel_lines():
    span = (bekker.column_start(1447, "a"), bekker.column_end(1447, "b"))
    c = chunk("poet.en.test", [("chapter", "1")], span, "chapter")
    assert locator(c) == "Poet. 1, 1447a–1447b"


def test_without_bekker_names_whose_division_it_is():
    c = chunk("eth-nic.es.azcarate", [("book", "1"), ("chapter", "11")], None, "none")
    assert locator(c) == "EN I.11 (división de la edición de Azcárate)"
    assert locator(c, "en") == "EN I.11 (division of Azcárate's edition)"
    # A range with precision "none" is not shown either.
    c2 = chunk("eth-nic.es.azcarate", [("book", "1")], "1094a1-1094a20", "none")
    assert locator(c2) == "EN I (división de la edición de Azcárate)"


def test_citation_fields_come_from_chunk_and_catalogue():
    c = chunk("oec.en.test", [("book", "1"), ("chapter", "1")], "1343a1-1343a25", "interpolated")
    cit = build_citation(3, c, WORKS, EDITIONS, lang="es")
    assert cit.n == 3
    assert cit.chunk_id == c.chunk_id
    assert cit.work_id == "oec"
    assert cit.work_title == "Económicos"
    assert cit.edition_id == "oec.en.test"
    assert cit.translator == "Test Translator"
    assert cit.lang.value == "en"
    assert cit.authenticity.value == "disputed"
    assert cit.licence == "CC BY-SA 4.0"
    assert cit.source_url == "https://example.org/oec.en.test.xml"
    assert (cit.bekker_start, cit.bekker_end) == bekker.parse_range("1343a1-1343a25")
    assert cit.text == "Texto del fragmento."
    assert build_citation(3, c, WORKS, EDITIONS, lang="en").work_title == "Economics"


def test_unknown_work_or_edition_is_an_error():
    c = chunk("eth-nic.en.test", NE_2_6, "1106b36-1107a8", "line")
    with pytest.raises(CitationError):
        build_citation(1, c, {}, EDITIONS)
    with pytest.raises(CitationError):
        build_citation(1, c, WORKS, {})


def test_roman():
    assert [roman(n) for n in (1, 4, 9, 10, 14, 30, 40)] == [
        "I", "IV", "IX", "X", "XIV", "XXX", "XL",
    ]  # fmt: skip


def test_real_catalogue_has_the_pilot_edition():
    catalogue = load_catalogue()
    assert catalogue.works["eth-nic"].abbr == "EN"
    rackham = catalogue.editions["eth-nic.en.rackham"]
    c = chunk("eth-nic.en.rackham", NE_2_6, "1106b36-1107a8", "interpolated")
    cit = build_citation(1, c, catalogue.works, catalogue.editions)
    assert cit.translator == rackham.translator == "Harris Rackham"
    assert cit.licence == "CC BY-SA 4.0"
    assert cit.source_url == rackham.url
    assert cit.locator == "EN II.6, 1106b36–1107a8 (aprox.)"
