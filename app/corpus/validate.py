"""`make validate`: the five checks of the Etapa 1 acceptance, and data/corpus_report.md.

1. campos: every unit has work, edition, language, location and text.
2. estructura: books and chapters match corpus/expected_structure.yaml.
3. ruido: no tags, entities, loose note calls or broken words left in text or notes.
4. bekker: ranges are monotone, inside the work's range and cover >= 99 % of its columns.
5. manifiesto: every indexed edition has translator, licence and evidence, and meets the
   admission rule.

Failures of indexed and reference editions make the exit code non-zero; editions in
waiting that were processed with --all are reported but only count with --strict.

    python -m app.corpus.validate [--strict] [--year YYYY]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app import bekker
from app.corpus import catalog
from app.models import Edition, SourceStatus, TextType, Unit, Work
from app.settings import Settings, get_settings

CHECKS = ("campos", "estructura", "ruido", "bekker", "manifiesto")
OK, FAIL, NA = "ok", "FALLA", "n/a"
MAX_EXAMPLES = 5

NOISE_PATTERNS: dict[str, re.Pattern[str]] = {
    "etiqueta": re.compile(r"<[^>\n]{1,80}>"),
    "entidad": re.compile(r"&(?:[A-Za-z]{2,8}|#\d{1,6}|#x[0-9A-Fa-f]{1,6});"),
    "llamada de nota": re.compile(r"\[\d{1,3}\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]|(?<=[A-Za-z]{2})\d{1,3}\b"),
    "marca interna": re.compile("[\ue000-\uf8ff]"),
    "palabra cortada": re.compile(r"\b[a-z]+- [a-z]+\b"),
    "espacios dobles": re.compile(r"[^\S\n]{2,}"),
}


@dataclass
class Check:
    status: str = OK
    problems: list[str] = field(default_factory=list)
    info: str = ""

    def fail(self, problem: str) -> None:
        self.status = FAIL
        self.problems.append(problem)


@dataclass
class EditionReport:
    edition: Edition
    units: list[Unit]
    counts: bool  # whether a failure here makes the exit code non-zero
    checks: dict[str, Check] = field(default_factory=dict)
    stats: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != FAIL for c in self.checks.values())


# --- the five checks ----------------------------------------------------------------------


def check_fields(units: list[Unit], edition: Edition, works: dict[str, Work]) -> Check:
    check = Check()
    if not units:
        check.fail("la edición no tiene unidades")
    seen: set[str] = set()
    last_seq = None
    for u in units:
        where = u.unit_id or f"seq {u.seq}"
        if not u.unit_id or not u.unit_id.startswith(f"{edition.edition_id}:"):
            check.fail(f"{where}: unit_id inválido")
        if u.unit_id in seen:
            check.fail(f"{where}: unit_id repetido")
        seen.add(u.unit_id)
        if u.work_id != edition.work_id or u.work_id not in works:
            check.fail(f"{where}: obra {u.work_id!r} no corresponde")
        if u.edition_id != edition.edition_id:
            check.fail(f"{where}: edición {u.edition_id!r} no corresponde")
        if u.lang != edition.lang:
            check.fail(f"{where}: idioma {u.lang} distinto del manifiesto ({edition.lang})")
        if not u.divisions or any(not label for _, label in u.divisions):
            check.fail(f"{where}: sin ubicación (divisiones vacías)")
        if not u.text.strip():
            check.fail(f"{where}: texto vacío")
        if last_seq is not None and u.seq <= last_seq:
            check.fail(f"{where}: seq no creciente")
        last_seq = u.seq
    return _trim(check)


def check_structure(units: list[Unit], expected: dict | None) -> Check:
    if expected is None:
        return Check(status=NA, info="sin estructura esperada para esta obra")
    check = Check()
    if "books" in expected:
        found: dict[str, int] = {}
        for u in units:
            if u.text_type == TextType.aristotle:
                book = u.divisions[0][1] if u.divisions else ""
                found[book] = found.get(book, 0) + 1
        want = {str(k): int(v) for k, v in expected["books"].items()}
        for book, n in want.items():
            if book not in found:
                check.fail(f"falta el libro {book} ({n} capítulos esperados)")
            elif found[book] != n:
                check.fail(f"libro {book}: {found[book]} unidades, se esperaban {n}")
        for book in found:
            if book not in want:
                check.fail(f"libro {book} no esperado ({found[book]} unidades)")
        check.info = f"{len(found)} libros, {sum(found.values())} capítulos"
    elif "units" in expected:
        n = sum(1 for u in units if u.text_type == TextType.aristotle)
        if n != int(expected["units"]):
            check.fail(f"{n} unidades, se esperaban {expected['units']}")
        check.info = f"{n} unidades"
    return check


def check_noise(units: list[Unit]) -> Check:
    check = Check()
    counts: dict[str, int] = {}
    for u in units:
        for field_name, value in [("texto", u.text)] + [("nota", n) for n in u.notes]:
            for name, pattern in NOISE_PATTERNS.items():
                for m in pattern.finditer(value):
                    counts[name] = counts.get(name, 0) + 1
                    if counts[name] <= MAX_EXAMPLES:
                        context = value[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ")
                        check.fail(f"{u.unit_id} ({field_name}) {name}: …{context}…")
    if counts:
        check.info = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    return check


def _column_index(value: int) -> int:
    ref = bekker.decode(value)
    return ref.page * 2 + (1 if ref.column == "b" else 0)


def check_bekker(
    units: list[Unit], edition: Edition, work: Work, min_coverage: float
) -> tuple[Check, dict[str, float]]:
    stats: dict[str, float] = {}
    if not edition.has_bekker:
        return Check(status=NA, info="la edición no trae Bekker"), stats
    check = Check()
    work_range = work.bekker_range()
    if work_range is None:
        check.fail(f"la obra {work.work_id} no tiene rango Bekker en works.yaml")
        return check, stats
    lo, hi = work_range
    covered: set[int] = set()
    prev: Unit | None = None
    anchors_outside = 0
    for u in units:
        if u.text_type != TextType.aristotle:
            continue
        if u.bekker_start is None or u.bekker_end is None:
            check.fail(f"{u.unit_id}: sin rango Bekker")
            continue
        rng = bekker.format_range(u.bekker_start, u.bekker_end)
        if u.bekker_end < u.bekker_start:
            check.fail(f"{u.unit_id}: rango invertido {rng}")
        if u.bekker_start < lo or u.bekker_end > hi:
            check.fail(f"{u.unit_id}: {rng} fuera del rango de la obra")
        if prev is not None and (
            u.bekker_start < prev.bekker_start or u.bekker_end < prev.bekker_end
        ):
            check.fail(
                f"{u.unit_id}: {rng} no es monótono tras {prev.unit_id} "
                f"({bekker.format_range(prev.bekker_start, prev.bekker_end)})"
            )
        covered.update(range(_column_index(u.bekker_start), _column_index(u.bekker_end) + 1))
        anchors_outside += sum(
            1 for _, pos in u.bekker_anchors if not u.bekker_start <= pos <= u.bekker_end
        )
        prev = u
    work_columns = set(range(_column_index(lo), _column_index(hi) + 1))
    coverage = len(covered & work_columns) / len(work_columns)
    stats["coverage"] = coverage
    stats["columns"] = len(work_columns)
    stats["anchors_outside"] = anchors_outside
    check.info = (
        f"cobertura {coverage:.1%} de {len(work_columns)} columnas; "
        f"marcas de la edición fuera del rango de su unidad: {anchors_outside}"
    )
    if coverage < min_coverage:
        missing = sorted(work_columns - covered)
        names = [f"{p}{'b' if r else 'a'}" for p, r in (divmod(c, 2) for c in missing)]
        check.fail(
            f"cobertura {coverage:.1%} < {min_coverage:.0%}; faltan {len(missing)} columnas: "
            f"{', '.join(names[:12])}"
        )
    return _trim(check), stats


def admission(edition: Edition, year: int, settings: Settings) -> tuple[bool, str]:
    """PLAN, "Regla de admisión": an explicit open licence, or US public domain plus a
    translator dead for more than 70 years."""
    open_licence = edition.licence in settings.admission_open_licences and bool(
        edition.licence_evidence.strip()
    )
    us_cutoff = year - settings.admission_us_pd_lag_years
    us_pd = edition.pub_year is not None and edition.pub_year <= us_cutoff
    dead = (
        edition.translator_death_year is not None
        and year - edition.translator_death_year > settings.admission_death_years
    )
    reasons = []
    if open_licence:
        reasons.append(f"licencia abierta explícita ({edition.licence})")
    if us_pd and dead:
        reasons.append(
            f"dominio público en EE. UU. ({edition.pub_year} <= {us_cutoff}) y traductor "
            f"muerto en {edition.translator_death_year}"
        )
    if reasons:
        return True, "; ".join(reasons)
    return False, "ni licencia abierta con evidencia ni dominio público con traductor muerto"


def check_manifest(edition: Edition, year: int, settings: Settings) -> Check:
    ok, reason = admission(edition, year, settings)
    if edition.status != SourceStatus.indexed:
        return Check(status=NA, info=f"no se indexa ({edition.status.value}); admisión: {reason}")
    check = Check(info=reason)
    if edition.lang.value != "grc" and not (edition.translator or "").strip():
        check.fail("falta el traductor")
    for name in ("licence", "licence_evidence", "attribution", "url", "pinned_version"):
        if not str(getattr(edition, name) or "").strip():
            check.fail(f"falta {name}")
    if not edition.sha256:
        check.fail("sha256 sin fijar (correr make corpus)")
    if not ok:
        check.fail(f"no cumple la regla de admisión: {reason}")
    return check


def _trim(check: Check) -> Check:
    if len(check.problems) > MAX_EXAMPLES * 4:
        extra = len(check.problems) - MAX_EXAMPLES * 4
        check.problems = check.problems[: MAX_EXAMPLES * 4] + [f"... y {extra} más"]
    return check


# --- run -----------------------------------------------------------------------------------


def _read_meta(edition_id: str, data_dir: Path) -> dict:
    path = catalog.meta_path(edition_id, data_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def validate(
    settings: Settings | None = None, *, year: int | None = None, strict: bool = False
) -> list[EditionReport]:
    settings = settings or get_settings()
    year = year or dt.date.today().year
    works = catalog.load_works(settings)
    editions = catalog.load_sources(settings)
    expected = catalog.load_expected_structure(settings)
    reports: list[EditionReport] = []
    for edition in editions:
        path = catalog.processed_path(edition.edition_id, settings.data_dir)
        must = edition.status == SourceStatus.indexed
        if not path.exists() and not must:
            continue
        counts = strict or edition.status in (SourceStatus.indexed, SourceStatus.reference)
        report = EditionReport(edition=edition, units=[], counts=counts)
        reports.append(report)
        if not path.exists():
            missing = Check()
            missing.fail(f"falta {path} (correr make corpus)")
            report.checks = {name: Check(status=NA) for name in CHECKS}
            report.checks["campos"] = missing
            report.checks["manifiesto"] = check_manifest(edition, year, settings)
            continue
        units = catalog.load_units(edition.edition_id, settings.data_dir)
        report.units = units
        work = works[edition.work_id]
        bekker_check, stats = check_bekker(
            units, edition, work, settings.corpus_min_bekker_coverage
        )
        report.checks = {
            "campos": check_fields(units, edition, works),
            "estructura": check_structure(units, expected.get(edition.work_id)),
            "ruido": check_noise(units),
            "bekker": bekker_check,
            "manifiesto": check_manifest(edition, year, settings),
        }
        report.stats = stats
        report.warnings = _read_meta(edition.edition_id, settings.data_dir).get("warnings", [])
    return reports


def _edition_row(r: EditionReport) -> str:
    units = [u for u in r.units if u.text_type == TextType.aristotle]
    words = sum(len(u.text.split()) for u in units)
    with_bekker = sum(1 for u in units if u.bekker_start is not None)
    pct = f"{with_bekker / len(units):.1%}" if units else "—"
    precision: dict[str, int] = {}
    for u in units:
        precision[u.bekker_precision.value] = precision.get(u.bekker_precision.value, 0) + 1
    prec = ", ".join(f"{k} {v}" for k, v in sorted(precision.items())) or "—"
    coverage = f"{r.stats['coverage']:.1%}" if "coverage" in r.stats else "—"
    notes = sum(len(u.notes) for u in r.units)
    return (
        f"| {r.edition.edition_id} | {r.edition.status.value} | {len(units)} | {words} | "
        f"{pct} | {coverage} | {prec} | {notes} |"
    )


def render_report(reports: list[EditionReport], settings: Settings) -> str:
    lines = [
        "# Reporte del corpus",
        "",
        "Generado por `python -m app.corpus.validate` (no editar a mano).",
        "",
        f"- corpus_version: `{catalog.corpus_version(settings)}`",
        f"- cobertura Bekker mínima: {settings.corpus_min_bekker_coverage:.0%} de las "
        "columnas de la obra",
        "",
        "## Ediciones",
        "",
        "| edición | estado | unidades | palabras | con Bekker | cobertura de columnas "
        "| precisión | notas |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [_edition_row(r) for r in reports]
    lines += [
        "",
        "## Comprobaciones",
        "",
        "| edición | cuenta | " + " | ".join(CHECKS) + " |",
        "|---|---|" + "---|" * len(CHECKS),
    ]
    for r in reports:
        cells = " | ".join(r.checks[name].status for name in CHECKS)
        lines.append(f"| {r.edition.edition_id} | {'sí' if r.counts else 'no'} | {cells} |")
    lines += ["", "## Detalle", ""]
    for r in reports:
        lines.append(f"### {r.edition.edition_id}")
        lines.append("")
        for name in CHECKS:
            check = r.checks[name]
            info = f": {check.info}" if check.info else ""
            lines.append(f"- {name}: {check.status}{info}")
            lines += [f"  - {p}" for p in check.problems]
        if r.warnings:
            lines.append("- avisos del adaptador:")
            lines += [f"  - {w}" for w in r.warnings]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida el corpus procesado.")
    parser.add_argument("--strict", action="store_true", help="las ediciones en espera cuentan")
    parser.add_argument("--year", type=int, help="año de referencia para la regla de admisión")
    args = parser.parse_args(argv)
    settings = get_settings()
    reports = validate(settings, year=args.year, strict=args.strict)
    report_path = settings.data_dir / "corpus_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(reports, settings) + "\n", encoding="utf-8")

    failed = False
    for r in reports:
        cells = " ".join(f"{name}={r.checks[name].status}" for name in CHECKS)
        print(f"{r.edition.edition_id} [{r.edition.status.value}]: {cells}")
        for name in CHECKS:
            for problem in r.checks[name].problems:
                print(f"    {name}: {problem}")
        failed |= r.counts and not r.ok
    if not reports:
        print("No hay ediciones procesadas: correr make corpus")
        failed = True
    print(f"Reporte: {report_path}")
    print("FALLA" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
