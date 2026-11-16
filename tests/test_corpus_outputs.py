"""Tests for the review sample and the generated ATTRIBUTION.md."""

from __future__ import annotations

from app import bekker
from app.corpus import attribution, catalog, sample
from app.models import Unit
from app.settings import ROOT, Settings


def make_unit(n: int, precision: str = "interpolated") -> Unit:
    return Unit(
        unit_id=f"eth-nic.en.rackham:1.{n}",
        work_id="eth-nic",
        edition_id="eth-nic.en.rackham",
        lang="en",
        divisions=[("book", "1"), ("chapter", str(n))],
        seq=n,
        bekker_start=bekker.parse("1094a1").to_int() + n,
        bekker_end=bekker.parse("1094a20").to_int() + n,
        bekker_precision=precision,
        text=" ".join(f"w{i}" for i in range(80)),
    )


def test_pick_is_deterministic_and_in_corpus_order():
    units = [make_unit(n) for n in range(1, 40)]
    first = sample.pick(units, 20, seed=1)
    assert first == sample.pick(units, 20, seed=1)
    assert len(first) == 20 and [u.seq for u in first] == sorted(u.seq for u in first)
    assert sample.pick(units, 20, seed=2) != first
    assert len(sample.pick(units[:5], 20, seed=1)) == 5


def test_locator_and_source_link():
    works = catalog.load_works()
    editions = catalog.editions_by_id()
    assert sample.locator(make_unit(2), works["eth-nic"]) == "EN 1.2, 1094a3–22 (aprox.)"
    assert sample.locator(make_unit(2, "line"), works["eth-nic"]) == "EN 1.2, 1094a3–22"
    link = sample.source_link(editions["eth-nic.en.rackham"], 172)
    assert link.startswith("https://github.com/PerseusDL/canonical-greekLit/blob/dacb049")
    assert link.endswith("tlg0086.tlg010.perseus-eng2.xml#L172")


def test_render_has_first_and_last_words(tmp_path):
    settings = Settings(data_dir=tmp_path)  # no raw file: link without a line anchor
    text = sample.render([make_unit(3)], catalog.load_works(), catalog.editions_by_id(), settings)
    assert "## 1. EN 1.3" in text
    assert "«w0 w1" in text and "w29»" in text and "«w50" in text and "w79»" in text


def test_attribution_lists_editions_changes_and_fixtures():
    text = attribution.render(
        catalog.load_sources(), catalog.load_works(), attribution.load_fixtures()
    )
    assert "Harris Rackham" in text and "CC BY-SA 4.0" in text
    assert "https://www.perseus.tufts.edu/" in text and "PerseusDL/canonical-greekLit" in text
    assert "## Qué se cambió" in text
    assert "tests/fixtures/perseus/ne_7_11_eng.xml" in text
    assert "&lt;licence" in text and "<licence" not in text


def test_fixtures_listed_exist():
    fixtures = attribution.load_fixtures()
    assert fixtures
    for f in fixtures:
        assert (ROOT / f["path"]).exists(), f["path"]


def test_attribution_file_is_up_to_date():
    expected = attribution.render(
        catalog.load_sources(), catalog.load_works(), attribution.load_fixtures()
    )
    current = (ROOT / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert current == expected, "regenerar con: uv run python -m app.corpus.attribution"
