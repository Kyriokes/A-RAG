"""Tests for app/corpus/validate.py: each of the five checks passes and fails as it should."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import bekker
from app.corpus import catalog, validate
from app.models import Edition, Unit
from app.settings import Settings

WORKS = """works:
  - work_id: w
    abbr: W
    title_es: Obra
    title_en: Work
    title_la: Opus
    bekker_start: 100a
    bekker_end: 101b
    authenticity: genuine
    structure: book_chapter
"""

SOURCES = """editions:
  - edition_id: w.en.t
    work_id: w
    lang: en
    translator: {translator}
    translator_death_year: 1944
    pub_year: {pub_year}
    url: https://example.org/w.xml
    pinned_version: x
    sha256: abc
    licence: {licence}
    licence_evidence: TEI licence line
    attribution: credit line
    adapter: perseus_tei
    tier: 0
    has_bekker: true
    status: indexed
"""

EXPECTED = """works:
  w:
    source: test
    books:
      "1": 2
      "2": 2
"""


def enc(ref: str) -> int:
    return bekker.parse(ref).to_int()


def unit(n: int, book: str, chapter: str, start: str, end: str, text: str = "Some text.") -> Unit:
    return Unit(
        unit_id=f"w.en.t:{book}.{chapter}",
        work_id="w",
        edition_id="w.en.t",
        lang="en",
        divisions=[("book", book), ("chapter", chapter)],
        seq=n,
        bekker_start=enc(start),
        bekker_end=enc(end),
        bekker_precision="interpolated",
        text=text,
    )


def good_units() -> list[Unit]:
    return [
        unit(1, "1", "1", "100a1", "100a30"),
        unit(2, "1", "2", "100a31", "100b20"),
        unit(3, "2", "1", "100b21", "101a35"),
        unit(4, "2", "2", "101b1", "101b30"),
    ]


def setup(
    tmp_path: Path,
    units: list[Unit],
    *,
    translator: str = "T",
    licence: str = "CC BY-SA 4.0",
    pub_year: int = 1926,
) -> Settings:
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    (corpus / "works.yaml").write_text(WORKS, encoding="utf-8")
    (corpus / "sources.yaml").write_text(
        SOURCES.format(translator=translator, licence=licence, pub_year=pub_year),
        encoding="utf-8",
    )
    (corpus / "expected_structure.yaml").write_text(EXPECTED, encoding="utf-8")
    settings = Settings(corpus_dir=corpus, data_dir=tmp_path / "data")
    catalog.write_units(units, catalog.processed_path("w.en.t", settings.data_dir))
    return settings


def statuses(settings: Settings) -> dict[str, str]:
    (report,) = validate.validate(settings, year=2026)
    return {name: check.status for name, check in report.checks.items()}


def test_good_corpus_passes(tmp_path):
    settings = setup(tmp_path, good_units())
    assert set(statuses(settings).values()) == {"ok"}


def test_main_writes_the_report_and_exits_zero(tmp_path, monkeypatch, capsys):
    settings = setup(tmp_path, good_units())
    monkeypatch.setattr(validate, "get_settings", lambda: settings)
    assert validate.main(["--year", "2026"]) == 0
    report = (settings.data_dir / "corpus_report.md").read_text(encoding="utf-8")
    assert "| w.en.t | indexed | 4 |" in report and "cobertura 100.0%" in report
    assert capsys.readouterr().out.strip().endswith("OK")


def test_main_exits_non_zero_on_failure(tmp_path, monkeypatch):
    units = good_units()
    units[0] = units[0].model_copy(update={"text": "  "})
    settings = setup(tmp_path, units)
    monkeypatch.setattr(validate, "get_settings", lambda: settings)
    assert validate.main(["--year", "2026"]) == 1


def test_missing_processed_file_fails(tmp_path):
    settings = setup(tmp_path, good_units())
    catalog.processed_path("w.en.t", settings.data_dir).unlink()
    assert statuses(settings)["campos"] == "FALLA"


def test_fields_failure(tmp_path):
    units = good_units()
    units[1] = units[1].model_copy(update={"divisions": [], "work_id": "other"})
    assert statuses(setup(tmp_path, units))["campos"] == "FALLA"


def test_structure_failure(tmp_path):
    units = good_units()[:3]  # book 2 has one chapter, two expected
    assert statuses(setup(tmp_path, units))["estructura"] == "FALLA"


@pytest.mark.parametrize(
    "noise",
    ["a <hi>tag</hi> here", "an &amp; entity", "a call[12] here", "stray note2 call", "x\ue000y"],
)
def test_noise_failure(tmp_path, noise):
    units = good_units()
    units[2] = units[2].model_copy(update={"text": f"Clean start, {noise}."})
    assert statuses(setup(tmp_path, units))["ruido"] == "FALLA"


def test_noise_in_notes_fails_too(tmp_path):
    units = good_units()
    units[0] = units[0].model_copy(update={"notes": ["note with <b>tag</b>"]})
    assert statuses(setup(tmp_path, units))["ruido"] == "FALLA"


def test_bekker_not_monotone(tmp_path):
    units = good_units()
    units[2] = units[2].model_copy(update={"bekker_start": enc("100a10")})
    assert statuses(setup(tmp_path, units))["bekker"] == "FALLA"


def test_bekker_outside_work_range(tmp_path):
    units = good_units()
    units[3] = units[3].model_copy(update={"bekker_end": enc("102a5")})
    assert statuses(setup(tmp_path, units))["bekker"] == "FALLA"


def test_bekker_coverage_below_threshold(tmp_path):
    units = good_units()
    units[2] = units[2].model_copy(update={"bekker_end": enc("100b30")})  # 101a not covered
    settings = setup(tmp_path, units)
    (report,) = validate.validate(settings, year=2026)
    assert report.checks["bekker"].status == "FALLA"
    assert report.stats["coverage"] == pytest.approx(0.75)


def test_bekker_missing_range(tmp_path):
    units = good_units()
    units[0] = units[0].model_copy(update={"bekker_start": None, "bekker_end": None})
    assert statuses(setup(tmp_path, units))["bekker"] == "FALLA"


def test_manifest_without_translator_fails(tmp_path):
    settings = setup(tmp_path, good_units(), translator="null")
    assert statuses(settings)["manifiesto"] == "FALLA"


def test_manifest_admission_rule(tmp_path):
    settings = setup(tmp_path, good_units(), licence="All rights reserved", pub_year=1935)
    assert statuses(settings)["manifiesto"] == "FALLA"


def _edition(**kw) -> Edition:
    base = {
        "edition_id": "w.en.t",
        "work_id": "w",
        "lang": "en",
        "translator": "T",
        "url": "u",
        "pinned_version": "x",
        "licence": "All rights reserved",
        "licence_evidence": "x",
        "attribution": "x",
        "adapter": "perseus_tei",
        "tier": 0,
        "has_bekker": True,
        "status": "indexed",
    }
    return Edition(**{**base, **kw})


@pytest.mark.parametrize(
    ("kw", "ok"),
    [
        ({"licence": "CC BY-SA 4.0"}, True),  # explicit open licence
        ({"pub_year": 1926, "translator_death_year": 1944}, True),  # US PD and dead > 70 y
        ({"pub_year": 1930, "translator_death_year": 1955}, True),  # both limits, 2026
        ({"pub_year": 1931, "translator_death_year": 1944}, False),  # after 1930
        ({"pub_year": 1926, "translator_death_year": 1956}, False),  # dead only 70 years
        ({"pub_year": 1926, "translator_death_year": None}, False),  # death unknown
        ({"licence": "CC BY-SA 4.0", "licence_evidence": " "}, False),  # no evidence
    ],
)
def test_admission(kw, ok):
    assert validate.admission(_edition(**kw), 2026, Settings())[0] is ok
