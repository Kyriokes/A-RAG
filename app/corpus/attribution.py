"""Generate ATTRIBUTION.md from the manifest (PLAN, "Obligaciones por usar Perseus").

python -m app.corpus.attribution
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from app.corpus import catalog
from app.models import Edition, SourceStatus, Work
from app.settings import ROOT, Settings, get_settings

FIXTURES_FILE = ROOT / "tests" / "fixtures" / "perseus" / "fixtures.yaml"

STATUS_ES = {
    SourceStatus.indexed: "indexada (se publica)",
    SourceStatus.reference: "referencia (no se publica)",
    SourceStatus.waiting: "en espera (no se publica)",
    SourceStatus.excluded: "excluida",
}

# What the pipeline does to a Perseus TEI text (app/corpus/adapters/perseus_tei.py).
CHANGES = [
    "Se extrae el texto del XML TEI; las etiquetas no se conservan.",
    "Las notas del traductor (`<note>`) y las referencias bibliográficas sueltas (`<bibl>`) "
    "se separan del texto y se guardan aparte.",
    "Se omiten los títulos de libro (`<head>`).",
    "Las secciones y subsecciones de la edición Loeb se unen en una unidad por capítulo "
    "(o por página Bekker en la Política), con un párrafo por bloque del TEI.",
    "Las citas marcadas (`<q>`) llevan comillas tipográficas según el TEI; los suplementos "
    "editoriales entre corchetes angulares se muestran como ⟨…⟩, las lagunas (`<gap>`) "
    "como …, y en `<choice>` se usa la lectura corregida.",
    "Se normalizan los espacios (repetidos, antes de la puntuación, después de una comilla "
    "de apertura y los que deja una nota quitada).",
    "Las marcas Bekker se conservan como metadatos. El rango Bekker de cada capítulo de "
    "una traducción se calcula desde el texto griego de Perseus del mismo commit.",
    "No se corrigió el texto. Si se corrige algo, la corrección se ofrece a Perseus.",
]


def load_fixtures(path: Path = FIXTURES_FILE) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("fixtures") or []


def _md(text: str) -> str:
    """Escape angle brackets so quoted TEI tags show up in Markdown."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _death(edition: Edition) -> str:
    if edition.translator is None:
        return ""
    if edition.translator_death_year is None:
        return " (año de muerte desconocido)"
    return f" (m. {edition.translator_death_year}, a confirmar)"


def _edition_section(edition: Edition, work: Work) -> list[str]:
    who = edition.translator or "texto original (sin traductor)"
    return [
        f"### {work.title_es} — `{edition.edition_id}`",
        "",
        f"- Obra: {work.title_es} ({work.title_en}; {work.title_la})",
        f"- Idioma: {edition.lang.value}",
        f"- Traductor: {who}{_death(edition)}",
        f"- Año de publicación: {edition.pub_year or 'desconocido'}",
        f"- Fuente: {edition.url}",
        f"- Versión fijada: `{edition.pinned_version}`"
        + (f" · sha256 `{edition.sha256}`" if edition.sha256 else ""),
        f"- Licencia: {edition.licence}",
        f"- Evidencia de la licencia: {_md(edition.licence_evidence)}",
        f"- Crédito requerido: {_md(edition.attribution)}",
        f"- Estado: {STATUS_ES[edition.status]}. {edition.status_reason}",
        "",
    ]


def render(editions: list[Edition], works: dict[str, Work], fixtures: list[dict]) -> str:
    order = [SourceStatus.indexed, SourceStatus.reference, SourceStatus.waiting]
    order.append(SourceStatus.excluded)
    lines = [
        "# Atribución",
        "",
        "Generado por `python -m app.corpus.attribution` desde `corpus/sources.yaml` y "
        "`tests/fixtures/perseus/fixtures.yaml`. No editar a mano.",
        "",
        "El código del proyecto se publica con licencia MIT (`LICENSE`). Los textos conservan "
        "su propia licencia, indicada abajo. El texto del corpus no se sube al repositorio: "
        "`make corpus` lo descarga y verifica su `sha256`. Las únicas excepciones son los "
        "fixtures de tests y las respuestas de ejemplo del front, listados al final. Si se "
        "publica el corpus procesado, va bajo CC BY-SA 4.0.",
        "",
        "## Perseus Digital Library",
        "",
        "Los textos vienen de Perseus Digital Library, Tufts University "
        "(https://www.perseus.tufts.edu/), repositorio "
        "[PerseusDL/canonical-greekLit](https://github.com/PerseusDL/canonical-greekLit), "
        "con licencia CC BY-SA 4.0. Obligaciones: citar traductor y edición, acreditar a "
        "Perseus con enlace, nombrar el repositorio de origen y la licencia, decir qué se "
        "cambió y ofrecer a Perseus las correcciones al texto.",
        "",
        "## Qué se cambió",
        "",
    ]
    lines += [f"- {c}" for c in CHANGES]
    lines += ["", "## Ediciones", ""]
    lines += ["| edición | obra | traductor o editor | año | licencia | estado |"]
    lines += ["|---|---|---|---|---|---|"]
    ranked = sorted(editions, key=lambda e: order.index(e.status))
    for e in ranked:
        who = e.translator or "texto griego"
        lines.append(
            f"| `{e.edition_id}` | {works[e.work_id].title_es} | {who} | {e.pub_year or '—'} "
            f"| {e.licence} | {STATUS_ES[e.status]} |"
        )
    lines.append("")
    for e in ranked:
        lines += _edition_section(e, works[e.work_id])
    lines += ["## Fixtures de tests en el repositorio", ""]
    if fixtures:
        lines += ["| archivo | edición | contenido | licencia |", "|---|---|---|---|"]
        for f in fixtures:
            lines.append(
                f"| `{f['path']}` | `{f.get('edition_id', '')}` | {f.get('content', '')} "
                f"| {f.get('licence', '')} |"
            )
    else:
        lines.append("Ninguno.")
    lines += ["", "## Respuestas de ejemplo del front", "", "Todavía no hay (Etapa 8).", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera ATTRIBUTION.md desde el manifiesto.")
    parser.add_argument("--out", type=Path, default=ROOT / "ATTRIBUTION.md")
    args = parser.parse_args(argv)
    settings = settings or get_settings()
    text = render(catalog.load_sources(settings), catalog.load_works(settings), load_fixtures())
    args.out.write_text(text, encoding="utf-8")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
