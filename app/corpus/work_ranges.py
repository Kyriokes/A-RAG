"""Derive each work's Bekker range, at column level, from its Greek text.

The range is the first and last Bekker column marked in the Greek file of the work (the
edition with lang grc in the manifest). It is never typed by hand.

    python -m app.corpus.work_ranges           # derived ranges vs corpus/works.yaml
    python -m app.corpus.work_ranges --write   # write them into corpus/works.yaml
"""

from __future__ import annotations

import argparse
import sys

from app import bekker
from app.corpus import catalog
from app.corpus.adapters.perseus_tei import parse_tei
from app.corpus.fetch import fetch
from app.models import Work
from app.settings import Settings, get_settings


def _column(value: int) -> str:
    ref = bekker.decode(value)
    return f"{ref.page}{ref.column}"


def derive(work: Work, settings: Settings | None = None) -> tuple[str, str] | None:
    """(first column, last column) marked in the work's Greek text, or None without one."""
    settings = settings or get_settings()
    editions = catalog.load_sources(settings)
    greek = next((e for e in editions if e.work_id == work.work_id and e.lang.value == "grc"), None)
    if greek is None or greek.adapter != "perseus_tei":
        return None
    path = fetch(greek, settings)
    depth = len(catalog.STRUCTURE_LEVELS[work.structure])
    positions = [pos for _, pos in parse_tei(path, unit_depth=depth).markers]
    if not positions:
        return None
    return _column(min(positions)), _column(max(positions))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rango Bekker de cada obra desde el griego.")
    parser.add_argument("--write", action="store_true", help="escribe corpus/works.yaml")
    args = parser.parse_args(argv)
    settings = get_settings()
    differ = False
    for work in catalog.load_works(settings).values():
        derived = derive(work, settings)
        if derived is None:
            print(f"{work.work_id}: sin texto griego con Bekker")
            continue
        current = (work.bekker_start, work.bekker_end)
        mark = "=" if current == derived else "≠"
        differ |= current != derived
        print(f"{work.work_id}: griego {derived[0]}–{derived[1]} {mark} catálogo {current}")
        if args.write and current != derived:
            path = catalog.works_path(settings)
            catalog.set_row_field(path, "work_id", work.work_id, "bekker_start", derived[0])
            catalog.set_row_field(path, "work_id", work.work_id, "bekker_end", derived[1])
    return 1 if differ and not args.write else 0


if __name__ == "__main__":
    sys.exit(main())
