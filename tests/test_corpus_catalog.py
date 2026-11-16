"""Tests for the catalogue, the manifest, load_units and `make corpus` (offline)."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.corpus import catalog, process
from app.models import Lang, SourceStatus, Structure
from app.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "perseus"
PINNED = "dacb04909e43dc47738828d33cc3d2b0635bc54c"


# --- the real corpus/*.yaml ------------------------------------------------------------------


def test_catalogue_has_the_seven_perseus_works():
    works = catalog.load_works()
    assert list(works) == ["eth-nic", "eth-eud", "pol", "metaph", "rhet", "poet", "oec"]
    assert works["oec"].authenticity.value == "disputed"
    assert all(w.authenticity.value == "genuine" for k, w in works.items() if k != "oec")
    assert works["pol"].structure == Structure.book_bekker_page
    assert works["poet"].structure == Structure.chapter
    for work in works.values():
        assert work.bekker_range() is not None, work.work_id  # derived from the Greek
        assert work.title_es and work.title_en and work.title_la and work.aliases
    assert {"Ética a Nicómaco", "Moral a Nicómaco", "Nicomachean Ethics", "EN", "Eth. Nic."} <= set(
        works["eth-nic"].aliases
    )


def test_manifest_rows():
    editions = catalog.load_sources()
    assert len(editions) == 14
    by_id = {e.edition_id: e for e in editions}
    indexed = [e.edition_id for e in editions if e.status == SourceStatus.indexed]
    assert indexed == ["eth-nic.en.rackham"]
    assert by_id["eth-nic.grc.bywater"].status == SourceStatus.reference
    for e in editions:
        assert PINNED in e.url and e.pinned_version == PINNED
        assert e.licence == "CC BY-SA 4.0" and e.licence_evidence and e.attribution
        assert e.adapter == "perseus_tei" and e.has_bekker
        if e.lang == Lang.en:
            assert e.translator
            assert catalog.reference_edition(e, editions).lang == Lang.grc
        if e.work_id != "eth-nic":
            assert e.status == SourceStatus.waiting and e.tier == 1
    english_waiting = [e for e in editions if e.status == SourceStatus.waiting and e.lang == "en"]
    assert all("Etapa 9" in e.status_reason for e in english_waiting)


def test_set_row_field_keeps_comments(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        "# header\neditions:\n  - edition_id: a.en.x\n    sha256: null  # first\n"
        "  - edition_id: b.en.y\n    sha256: null\n    url: u\n",
        encoding="utf-8",
    )
    catalog.set_row_field(path, "edition_id", "b.en.y", "sha256", "abc")
    assert path.read_text(encoding="utf-8") == (
        "# header\neditions:\n  - edition_id: a.en.x\n    sha256: null  # first\n"
        "  - edition_id: b.en.y\n    sha256: abc\n    url: u\n"
    )


# --- a temporary corpus ----------------------------------------------------------------------

WORKS_YAML = """works:
  - work_id: eth-nic
    abbr: EN
    title_es: Ética a Nicómaco
    title_en: Nicomachean Ethics
    title_la: Ethica Nicomachea
    aliases: [EN]
    bekker_start: 1094a
    bekker_end: 1181b
    authenticity: genuine
    structure: book_chapter
    perseus_id: tlg010
"""

SOURCES_YAML = """editions:
  - edition_id: eth-nic.en.rackham
    work_id: eth-nic
    lang: en
    translator: Harris Rackham
    translator_death_year: 1944
    pub_year: 1926
    url: https://example.org/eng.xml
    pinned_version: x
    sha256: null
    licence: CC BY-SA 4.0
    licence_evidence: TEI licence line
    attribution: Rackham, Perseus, CC BY-SA 4.0
    adapter: perseus_tei
    tier: 0
    has_bekker: true
    status: indexed
  - edition_id: eth-nic.en.waiting
    work_id: eth-nic
    lang: en
    translator: Someone
    url: https://example.org/w.xml
    pinned_version: x
    licence: CC BY-SA 4.0
    licence_evidence: x
    attribution: x
    adapter: perseus_tei
    tier: 1
    has_bekker: true
    status: waiting
"""


def make_corpus(tmp_path: Path) -> Settings:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "works.yaml").write_text(WORKS_YAML, encoding="utf-8")
    (corpus / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    shutil.copy(FIXTURES / "ne_7_11_eng.xml", raw / "eth-nic.en.rackham.xml")
    return Settings(corpus_dir=corpus, data_dir=tmp_path / "data", bekker_column_lines=35)


def test_select_editions():
    editions = catalog.load_sources()
    default = [e.edition_id for e in process.select_editions(editions)]
    assert default == ["eth-nic.en.rackham", "eth-nic.grc.bywater"]
    tier1 = process.select_editions(editions, tier=1)
    assert len(tier1) == 14
    assert len(process.select_editions(editions, all_editions=True)) == 14


def test_process_offline_is_deterministic(tmp_path):
    settings = make_corpus(tmp_path)
    editions = catalog.load_sources(settings)
    edition = editions[0]
    summary = process.process_edition(edition, editions, settings, offline=True)
    assert summary.units == 1 and summary.with_bekker == 1 and summary.error is None
    out = catalog.processed_path(edition.edition_id, settings.data_dir)
    first = out.read_bytes()
    process.process_edition(edition, editions, settings, offline=True)
    assert out.read_bytes() == first
    meta = catalog.meta_path(edition.edition_id, settings.data_dir).read_text(encoding="utf-8")
    assert '"tei_levels": [\n    "book",\n    "section",\n    "subsection"\n  ]' in meta


def test_load_units_round_trip(tmp_path):
    settings = make_corpus(tmp_path)
    editions = catalog.load_sources(settings)
    process.process_edition(editions[0], editions, settings, offline=True)
    units = catalog.load_units("eth-nic.en.rackham", settings.data_dir)
    assert [u.unit_id for u in units] == ["eth-nic.en.rackham:7.11"]
    assert units[0].divisions == [("book", "7"), ("chapter", "11")]
    assert units[0].bekker_anchors[0] == (0, 230501)
    # edition_id=None reads every indexed edition of the real manifest from data_dir.
    assert [u.unit_id for u in catalog.load_units(data_dir=settings.data_dir)] == [
        "eth-nic.en.rackham:7.11"
    ]


def test_load_units_missing_file(tmp_path):
    try:
        catalog.load_units("nope.en.x", tmp_path)
    except FileNotFoundError as exc:
        assert "make corpus" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
