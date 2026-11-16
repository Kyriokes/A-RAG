"""Catalogue (corpus/works.yaml), manifest (corpus/sources.yaml) and processed units.

The YAML files are the single source of truth for which texts exist, where they come from,
under which licence and whether they are indexed (PLAN, "Contratos de datos").
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from app.models import Edition, SourceStatus, Structure, Unit, Work
from app.settings import Settings, get_settings

WORKS_FILE = "works.yaml"
SOURCES_FILE = "sources.yaml"
EXPECTED_FILE = "expected_structure.yaml"

# Canonical names of the citation levels of each structure, outermost first. The adapter
# reads the actual division types from each source and maps them onto these names, so
# every unit of a work uses the same level names whatever the source calls them.
STRUCTURE_LEVELS: dict[Structure, tuple[str, ...]] = {
    Structure.book_chapter: ("book", "chapter"),
    Structure.chapter: ("chapter",),
    Structure.book_bekker_page: ("book", "bekker_page"),
    Structure.book_problem: ("book", "problem"),
}


class CatalogError(ValueError):
    """Raised when works.yaml or sources.yaml is malformed or inconsistent."""


def _settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def works_path(settings: Settings | None = None) -> Path:
    return _settings(settings).corpus_dir / WORKS_FILE


def sources_path(settings: Settings | None = None) -> Path:
    return _settings(settings).corpus_dir / SOURCES_FILE


def expected_structure_path(settings: Settings | None = None) -> Path:
    return _settings(settings).corpus_dir / EXPECTED_FILE


def load_works(settings: Settings | None = None) -> dict[str, Work]:
    """All works of the catalogue, keyed by work_id, in file order."""
    data = _read_yaml(works_path(settings)) or {}
    rows = data.get("works") or []
    works: dict[str, Work] = {}
    for row in rows:
        work = Work.model_validate(row)
        if work.work_id in works:
            raise CatalogError(f"work_id repetido en works.yaml: {work.work_id}")
        if (work.bekker_start is None) != (work.bekker_end is None):
            raise CatalogError(f"{work.work_id}: bekker_start y bekker_end van juntos")
        work.bekker_range()  # raises BekkerError on a malformed reference
        works[work.work_id] = work
    return works


def load_sources(settings: Settings | None = None) -> list[Edition]:
    """All editions of the manifest, in file order, checked against the catalogue."""
    data = _read_yaml(sources_path(settings)) or {}
    rows = data.get("editions") or []
    works = load_works(settings)
    editions: list[Edition] = []
    seen: set[str] = set()
    for row in rows:
        edition = Edition.model_validate(row)
        if edition.edition_id in seen:
            raise CatalogError(f"edition_id repetido en sources.yaml: {edition.edition_id}")
        if edition.work_id not in works:
            raise CatalogError(f"{edition.edition_id}: work_id desconocido {edition.work_id}")
        seen.add(edition.edition_id)
        editions.append(edition)
    return editions


def editions_by_id(settings: Settings | None = None) -> dict[str, Edition]:
    return {e.edition_id: e for e in load_sources(settings)}


def load_expected_structure(settings: Settings | None = None) -> dict[str, dict]:
    """Expected books and chapter counts per work (corpus/expected_structure.yaml)."""
    path = expected_structure_path(settings)
    if not path.exists():
        return {}
    data = _read_yaml(path) or {}
    return data.get("works") or {}


def reference_edition(edition: Edition, editions: list[Edition]) -> Edition | None:
    """The Greek text that gives Bekker ranges to `edition`, if the manifest has one."""
    if edition.lang.value == "grc":
        return None
    for other in editions:
        if (
            other.work_id == edition.work_id
            and other.lang.value == "grc"
            and other.has_bekker
            and other.status != SourceStatus.excluded
        ):
            return other
    return None


# --- data files --------------------------------------------------------------------------


def raw_path(edition: Edition, settings: Settings | None = None) -> Path:
    """Where fetch.py stores the downloaded source of an edition."""
    suffix = Path(edition.url.split("?")[0]).suffix or ".dat"
    return _settings(settings).data_dir / "raw" / f"{edition.edition_id}{suffix}"


def processed_path(edition_id: str, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else get_settings().data_dir
    return base / "processed" / f"{edition_id}.jsonl"


def meta_path(edition_id: str, data_dir: Path | None = None) -> Path:
    """Side file written by process.py: source checksum, TEI header facts, warnings."""
    base = data_dir if data_dir is not None else get_settings().data_dir
    return base / "processed" / f"{edition_id}.meta.json"


def write_units(units: list[Unit], path: Path) -> None:
    """Write units as JSON Lines, one unit per line, in a deterministic form."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [u.model_dump_json() for u in units]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    tmp.replace(path)


def load_units(edition_id: str | None = None, data_dir: Path | None = None) -> list[Unit]:
    """Units of one edition, or of every indexed edition (manifest order) when None.

    Reads data/processed/<edition_id>.jsonl, written by `make corpus`. Raises
    FileNotFoundError when an edition has not been processed yet.
    """
    if edition_id is not None:
        ids = [edition_id]
    else:
        ids = [e.edition_id for e in load_sources() if e.status == SourceStatus.indexed]
    units: list[Unit] = []
    for eid in ids:
        path = processed_path(eid, data_dir)
        if not path.exists():
            raise FileNotFoundError(f"{path} no existe: correr `make corpus`")
        with path.open(encoding="utf-8") as fh:
            units.extend(Unit.model_validate_json(line) for line in fh if line.strip())
    return units


def corpus_version(settings: Settings | None = None) -> str:
    """Hash of the catalogue, the manifest and the processed files of indexed editions."""
    s = _settings(settings)
    h = hashlib.sha256()
    for path in (works_path(s), sources_path(s)):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    for edition in load_sources(s):
        if edition.status != SourceStatus.indexed:
            continue
        path = processed_path(edition.edition_id, s.data_dir)
        h.update(edition.edition_id.encode())
        h.update(path.read_bytes() if path.exists() else b"<missing>")
    return h.hexdigest()[:16]


def set_row_field(path: Path, id_field: str, id_value: str, field: str, value: str) -> None:
    """Set `field: value` in the YAML list row whose `id_field` is `id_value`.

    Edits the text line by line so comments and layout survive; the field must already be
    present in that row.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start_re = re.compile(rf"^\s*-\s*{re.escape(id_field)}:\s*['\"]?([^'\"\s#]+)")
    field_re = re.compile(rf"^(\s*){re.escape(field)}:[^\n]*?(\r?\n)?$")
    inside = False
    for i, line in enumerate(lines):
        m = start_re.match(line)
        if m:
            inside = m.group(1) == id_value
            continue
        if inside and line.strip() and not line.startswith((" ", "\t")):
            inside = False  # left the list
        if inside:
            f = field_re.match(line)
            if f:
                lines[i] = f"{f.group(1)}{field}: {value}{f.group(2) or ''}"
                path.write_text("".join(lines), encoding="utf-8")
                return
    raise CatalogError(f"{path}: no encuentro {field} en la fila {id_field}={id_value}")
