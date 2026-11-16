"""Validity of the eval labels (PLAN, Etapa 3).

Two levels:

- `check_format`: needs only the dataset and the catalogue (corpus/works.yaml), so it runs
  in `make test` without corpus, network or LLM. Schema, unique ids, `twin_of`, works in
  the catalogue, Bekker spans inside the work's range, and no evidence passage in both
  `dev` and `test` (the split is by passage, and twins stay in the same split).
- `check_corpus`: every span resolves to non-empty text of the current processed corpus
  (it overlaps some unit of that work, or matches its book and chapter).

`python -m evals.labels` runs both; `--format-only` skips the corpus.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.models import TextType, Unit, Work
from evals.metrics import Location, MatchParams, span_hit, spans_overlap
from evals.schema import DATASET_DIR, BekkerSpan, Case, LoadedDataset, Span, load_dataset

# "Resolves to text" means sharing at least one line, with no tolerance.
_RESOLVE = MatchParams(tolerance_lines=0, min_overlap_lines=1)


def _spans(case: Case) -> list[tuple[int, int, Span]]:
    return [(g, i, span) for g, group in enumerate(case.evidence) for i, span in enumerate(group)]


def check_format(dataset: LoadedDataset, works: dict[str, Work] | None = None) -> list[str]:
    """Problems that need no corpus. `works`: the catalogue, to check ids and ranges."""
    errors = list(dataset.errors)
    cases = dataset.cases
    seen: dict[str, Case] = {}
    for case in cases:
        if case.id in seen:
            errors.append(f"{case.id}: id repetido")
        seen[case.id] = case
    for case in cases:
        if case.twin_of is None:
            continue
        twin = seen.get(case.twin_of)
        if twin is None:
            errors.append(f"{case.id}: twin_of={case.twin_of} no existe")
        elif twin.split != case.split:
            errors.append(f"{case.id}: twin_of={case.twin_of} está en otro conjunto")
    if works is not None:
        for case in cases:
            for g, i, span in _spans(case):
                where = f"{case.id}: evidencia [{g}][{i}] {span.label()}"
                work = works.get(span.work_id)
                if work is None:
                    errors.append(f"{where}: obra desconocida")
                    continue
                if isinstance(span, BekkerSpan):
                    work_range = work.bekker_range()
                    if work_range is None:
                        errors.append(f"{where}: la obra no tiene numeración Bekker")
                    else:
                        start, end = span.encoded()
                        if start < work_range[0] or end > work_range[1]:
                            errors.append(
                                f"{where}: fuera del rango de la obra "
                                f"({work.bekker_start}–{work.bekker_end})"
                            )
    errors.extend(split_leaks(cases))
    return errors


def split_leaks(cases: Sequence[Case]) -> list[str]:
    """Evidence passages that appear in both dev and test."""
    dev = [(c.id, s) for c in cases if c.split == "dev" for _, _, s in _spans(c)]
    test = [(c.id, s) for c in cases if c.split == "test" for _, _, s in _spans(c)]
    problems = []
    for test_id, t in test:
        for dev_id, d in dev:
            if spans_overlap(t, d):
                problems.append(
                    f"{test_id} (test) y {dev_id} (dev) comparten el pasaje "
                    f"{t.label()} / {d.label()}"
                )
    return problems


def unit_location(unit: Unit) -> Location:
    return Location(
        chunk_id=unit.unit_id,
        work_id=unit.work_id,
        edition_id=unit.edition_id,
        lang=unit.lang.value,
        bekker_start=unit.bekker_start,
        bekker_end=unit.bekker_end,
        divisions=tuple((a, b) for a, b in unit.divisions),
    )


def check_corpus(cases: Sequence[Case], units: Sequence[Unit]) -> list[str]:
    """Every span must overlap some non-empty unit of the Aristotle text of its work."""
    by_work: dict[str, list[Location]] = {}
    for unit in units:
        if unit.text_type == TextType.aristotle and unit.text.strip():
            by_work.setdefault(unit.work_id, []).append(unit_location(unit))
    errors = []
    for case in cases:
        for g, i, span in _spans(case):
            locations = by_work.get(span.work_id, [])
            if not any(span_hit(loc, span, _RESOLVE) for loc in locations):
                errors.append(
                    f"{case.id}: evidencia [{g}][{i}] {span.label()} no resuelve a texto del "
                    "corpus procesado"
                )
    return errors


def evidence_clusters(cases: Sequence[Case]) -> dict[str, str]:
    """Case id -> cluster id: cases whose evidence spans overlap share a cluster (for the
    clustered bootstrap). The cluster id is the smallest case id in it."""
    parent = {c.id: c.id for c in cases}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    spans = [(c.id, s) for c in cases for _, _, s in _spans(c)]
    for i, (id_a, a) in enumerate(spans):
        for id_b, b in spans[i + 1 :]:
            if id_a != id_b and spans_overlap(a, b):
                ra, rb = find(id_a), find(id_b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    return {c.id: find(c.id) for c in cases}


def validate_labels(
    dataset: LoadedDataset,
    *,
    works: dict[str, Work],
    units: Sequence[Unit] | None,
    reviewed_only: bool = True,
) -> list[str]:
    """Format checks on every case, corpus checks on reviewed cases (or all)."""
    errors = check_format(dataset, works)
    if units is not None:
        cases = dataset.reviewed if reviewed_only else dataset.cases
        errors.extend(check_corpus(cases, units))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida las etiquetas del dataset de evals.")
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument(
        "--format-only", action="store_true", help="sin corpus: solo formato y catálogo"
    )
    args = parser.parse_args(argv)

    from app.corpus.catalog import load_units, load_works

    dataset = load_dataset(args.dataset_dir)
    works = load_works()
    units = None if args.format_only else load_units()
    errors = validate_labels(dataset, works=works, units=units, reviewed_only=False)
    print(
        f"Dataset {dataset.version}: {len(dataset.cases)} casos en {len(dataset.files)} "
        f"archivos ({len(dataset.reviewed)} revisados, {len(dataset.unreviewed)} sin revisar)."
    )
    for error in errors:
        print(f"  ERROR {error}")
    print("Etiquetas válidas." if not errors else f"{len(errors)} problemas.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
