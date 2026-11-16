"""`make corpus`: download, verify and adapt every edition into data/processed/.

By default it processes the indexed editions plus the Greek texts they take Bekker ranges
from. ``--tier N`` adds the editions in waiting of tier N, ``--all`` every edition that is
not excluded. Output is deterministic: the same sources give byte-identical files.

    python -m app.corpus.process [--tier N | --all] [--offline]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.corpus import catalog
from app.corpus.adapters import ADAPTERS
from app.corpus.fetch import fetch, sha256_file
from app.models import Edition, SourceStatus, Unit
from app.settings import Settings, get_settings

LICENCE_URLS = {"CC BY-SA 4.0": "https://creativecommons.org/licenses/by-sa/4.0/"}


@dataclass
class EditionSummary:
    edition_id: str
    units: int = 0
    words: int = 0
    with_bekker: int = 0
    precision: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def select_editions(
    editions: list[Edition], *, tier: int | None = None, all_editions: bool = False
) -> list[Edition]:
    """Indexed editions, the waiting ones asked for, and the references they need."""
    wanted: set[str] = set()
    for e in editions:
        asked = e.status == SourceStatus.waiting and (all_editions or e.tier == tier)
        if e.status == SourceStatus.indexed or asked:
            wanted.add(e.edition_id)
    for e in editions:
        if e.edition_id in wanted:
            ref = catalog.reference_edition(e, editions)
            if ref is not None:
                wanted.add(ref.edition_id)
    return [e for e in editions if e.edition_id in wanted]


def _local_copy(edition: Edition, settings: Settings, offline: bool) -> Path:
    if offline:
        path = catalog.raw_path(edition, settings)
        if not path.exists():
            raise FileNotFoundError(f"{path} no existe y se pidió --offline")
        return path
    return fetch(edition, settings)


def _header_checks(edition: Edition, header: dict[str, str]) -> list[str]:
    """Compare the manifest with the TEI header; differences are reported, not fixed."""
    out = []
    url = LICENCE_URLS.get(edition.licence)
    if url and header.get("licence_target") != url:
        out.append(f"licencia del TEI ({header.get('licence_target')!r}) distinta de {url}")
    if edition.translator and header.get("editor") and edition.translator != header["editor"]:
        out.append(f"traductor del TEI ({header['editor']!r}) distinto del manifiesto")
    return out


def summarize(edition_id: str, units: list[Unit], warnings: list[str]) -> EditionSummary:
    summary = EditionSummary(edition_id=edition_id, units=len(units), warnings=warnings)
    for u in units:
        summary.words += len(u.text.split())
        if u.bekker_start is not None:
            summary.with_bekker += 1
        key = u.bekker_precision.value
        summary.precision[key] = summary.precision.get(key, 0) + 1
    return summary


def process_edition(
    edition: Edition,
    editions: list[Edition],
    settings: Settings | None = None,
    *,
    offline: bool = False,
) -> EditionSummary:
    settings = settings or get_settings()
    works = catalog.load_works(settings)
    work = works[edition.work_id]
    raw = _local_copy(edition, settings, offline)
    reference = catalog.reference_edition(edition, editions)
    ref_raw = _local_copy(reference, settings, offline) if reference is not None else None
    result = ADAPTERS[edition.adapter](raw, edition, work, ref_raw, settings)
    warnings = list(result.warnings) + _header_checks(edition, result.header)

    catalog.write_units(result.units, catalog.processed_path(edition.edition_id, settings.data_dir))
    summary = summarize(edition.edition_id, result.units, warnings)
    meta = {
        "edition_id": edition.edition_id,
        "adapter": edition.adapter,
        "source_sha256": sha256_file(raw),
        "reference_edition_id": reference.edition_id if reference else None,
        "reference_sha256": sha256_file(ref_raw) if ref_raw else None,
        "tei_levels": result.level_types,
        "tei_header": result.header,
        "units": summary.units,
        "words": summary.words,
        "warnings": warnings,
    }
    meta_file = catalog.meta_path(edition.edition_id, settings.data_dir)
    meta_file.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _format_summary(s: EditionSummary) -> str:
    if s.error:
        return f"{s.edition_id}: ERROR {s.error}"
    pct = 100 * s.with_bekker / s.units if s.units else 0.0
    precision = ", ".join(f"{k} {v}" for k, v in sorted(s.precision.items()))
    return (
        f"{s.edition_id}: {s.units} unidades, {s.words} palabras, "
        f"con Bekker {pct:.1f}% ({precision}), avisos {len(s.warnings)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Descarga y procesa el corpus.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--tier", type=int, help="suma las ediciones en espera de ese nivel")
    group.add_argument("--all", action="store_true", help="suma todas las ediciones en espera")
    parser.add_argument("--offline", action="store_true", help="usa solo data/raw/, sin red")
    args = parser.parse_args(argv)

    settings = get_settings()
    editions = catalog.load_sources(settings)
    selected = select_editions(editions, tier=args.tier, all_editions=args.all)
    failed = False
    for edition in selected:
        try:
            # Re-read the manifest: fetch may have just recorded a sha256 in it.
            current = catalog.editions_by_id(settings)[edition.edition_id]
            summary = process_edition(
                current, catalog.load_sources(settings), settings, offline=args.offline
            )
        except Exception as exc:  # report every edition, then fail
            summary = EditionSummary(edition.edition_id, error=f"{type(exc).__name__}: {exc}")
            failed = True
        print(_format_summary(summary))
        for warning in summary.warnings:
            print(f"    aviso: {warning}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
