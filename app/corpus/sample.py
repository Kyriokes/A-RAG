"""`make sample`: random units for the person's review (PLAN, Etapa 1).

Writes data/sample_review.md with each unit's location, its first and last 30 words and a
link to the exact line of the source file at the pinned commit.

    python -m app.corpus.sample --n 20 --seed 1 [--edition EDITION_ID]
"""

from __future__ import annotations

import argparse
import random
import re
import sys

from app import bekker
from app.corpus import catalog
from app.corpus.adapters.perseus_tei import parse_tei
from app.models import BekkerPrecision, Edition, SourceStatus, TextType, Unit, Work
from app.settings import Settings, get_settings

EDGE_WORDS = 30
_RAW_RE = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$")


def locator(unit: Unit, work: Work) -> str:
    """Short human location, e.g. "EN 2.6, 1106a14–1107a27" (the citation module owns the
    final format; this one is only for review)."""
    labels = ".".join(label for _, label in unit.divisions)
    rng = bekker.format_range(
        unit.bekker_start,
        unit.bekker_end,
        approximate=unit.bekker_precision != BekkerPrecision.line,
    )
    return f"{work.abbr} {labels}" + (f", {rng}" if rng else "")


def source_link(edition: Edition, sourceline: int | None) -> str:
    """GitHub page of the pinned source file, at the unit's line when known."""
    m = _RAW_RE.match(edition.url)
    if not m:
        return edition.url
    owner, repo, ref, path = m.groups()
    url = f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"
    return f"{url}#L{sourceline}" if sourceline else url


def _sourcelines(edition: Edition, work: Work, settings: Settings) -> dict[str, int | None]:
    raw = catalog.raw_path(edition, settings)
    if edition.adapter != "perseus_tei" or not raw.exists():
        return {}
    depth = len(catalog.STRUCTURE_LEVELS[work.structure])
    parsed = parse_tei(raw, unit_depth=depth)
    return {".".join(u.path): u.sourceline for u in parsed.units}


def pick(units: list[Unit], n: int, seed: int) -> list[Unit]:
    """`n` random units (all of them if fewer), in corpus order."""
    candidates = [u for u in units if u.text_type == TextType.aristotle]
    chosen = random.Random(seed).sample(candidates, min(n, len(candidates)))
    order = {u.unit_id: i for i, u in enumerate(candidates)}
    return sorted(chosen, key=lambda u: order[u.unit_id])


def render(
    units: list[Unit], works: dict[str, Work], editions: dict[str, Edition], settings: Settings
) -> str:
    lines_by_edition: dict[str, dict[str, int | None]] = {}
    out = [
        "# Muestra para revisión",
        "",
        "Generado por `python -m app.corpus.sample`. Para cada unidad: abrir el enlace, "
        "comprobar que la ubicación y el rango Bekker son correctos y que no quedó ninguna "
        "nota del traductor mezclada en el texto. Cada falla se corrige en el adaptador y se "
        "agrega como test (PLAN, Etapa 1).",
        "",
    ]
    for i, u in enumerate(units, start=1):
        edition = editions[u.edition_id]
        work = works[u.work_id]
        if u.edition_id not in lines_by_edition:
            lines_by_edition[u.edition_id] = _sourcelines(edition, work, settings)
        path = u.unit_id.split(":", 1)[1]
        link = source_link(edition, lines_by_edition[u.edition_id].get(path))
        words = u.text.split()
        head = " ".join(words[:EDGE_WORDS])
        tail = " ".join(words[-EDGE_WORDS:])
        out += [
            f"## {i}. {locator(u, work)}",
            "",
            f"- unit_id: `{u.unit_id}` · precisión Bekker: {u.bekker_precision.value} · "
            f"{len(words)} palabras · {len(u.notes)} notas",
            f"- fuente: {link}",
            f"- comienzo: «{head}»",
            f"- final: «{tail}»",
            "- [ ] ubicación correcta  - [ ] sin notas mezcladas",
            "",
        ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muestra de unidades para revisión.")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--edition", help="una edición; por defecto, las indexadas")
    args = parser.parse_args(argv)
    settings = get_settings()
    editions = catalog.editions_by_id(settings)
    if args.edition:
        ids = [args.edition]
    else:
        ids = [e.edition_id for e in editions.values() if e.status == SourceStatus.indexed]
    units: list[Unit] = []
    for edition_id in ids:
        units.extend(catalog.load_units(edition_id, settings.data_dir))
    chosen = pick(units, args.n, args.seed)
    path = settings.data_dir / "sample_review.md"
    text = render(chosen, catalog.load_works(settings), editions, settings)
    path.write_text(text + "\n", encoding="utf-8")
    print(f"{len(chosen)} unidades (seed {args.seed}) -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
