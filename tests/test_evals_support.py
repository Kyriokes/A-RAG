"""Helpers for the evals tests (no tests here).

A tiny synthetic corpus and dataset: invented sentences, not Aristotle's text.
"""

from __future__ import annotations

import json
from pathlib import Path

from app import bekker
from app.models import Chunk, Citation, RetrievedChunk, Unit, Work

WORKS = {
    "eth-nic": Work(
        work_id="eth-nic",
        abbr="EN",
        title_es="Ética a Nicómaco",
        title_en="Nicomachean Ethics",
        title_la="Ethica Nicomachea",
        bekker_start="1094a",
        bekker_end="1181b",
        authenticity="genuine",
        structure="book_chapter",
    ),
    "oec": Work(
        work_id="oec",
        abbr="Oec.",
        title_es="Económicos",
        title_en="Economics",
        title_la="Oeconomica",
        bekker_start="1343a",
        bekker_end="1353b",
        authenticity="disputed",
        structure="book_chapter",
    ),
    "nobekker": Work(
        work_id="nobekker",
        abbr="NB",
        title_es="Obra sin Bekker",
        title_en="Work without Bekker",
        title_la="Opus",
        authenticity="genuine",
        structure="chapter",
    ),
}

SYNTHETIC_TEXT = "Synthetic test sentence number {n}, written for the eval tests."

# (unit_id, work_id, edition_id, divisions, Bekker range or None)
_UNITS = [
    (
        "eth-nic.en.t:2.6",
        "eth-nic",
        "eth-nic.en.t",
        [("book", "2"), ("chapter", "6")],
        "1106a14-1107a27",
    ),
    (
        "eth-nic.en.t:8.3",
        "eth-nic",
        "eth-nic.en.t",
        [("book", "8"), ("chapter", "3")],
        "1156a6-1156b32",
    ),
    (
        "eth-nic.en.t:10.7",
        "eth-nic",
        "eth-nic.en.t",
        [("book", "10"), ("chapter", "7")],
        "1177a12-1178a8",
    ),
    ("oec.en.t:1.1", "oec", "oec.en.t", [("book", "1"), ("chapter", "1")], "1343a1-1343b6"),
    ("nobekker.es.t:3", "nobekker", "nobekker.es.t", [("chapter", "3")], None),
]


def units() -> list[Unit]:
    out = []
    for n, (unit_id, work_id, edition_id, divisions, span) in enumerate(_UNITS, start=1):
        start, end = bekker.parse_range(span) if span else (None, None)
        out.append(
            Unit(
                unit_id=unit_id,
                work_id=work_id,
                edition_id=edition_id,
                lang=edition_id.split(".")[1],
                divisions=divisions,
                seq=n,
                bekker_start=start,
                bekker_end=end,
                bekker_precision="interpolated" if span else "none",
                bekker_anchors=[(0, start)] if start else [],
                text=SYNTHETIC_TEXT.format(n=n),
            )
        )
    return out


def chunk(
    chunk_id: str,
    span: str | None,
    *,
    work_id: str = "eth-nic",
    edition_id: str = "eth-nic.en.t",
    divisions: list[tuple[str, str]] | None = None,
    text: str = "chunk text",
) -> Chunk:
    start, end = bekker.parse_range(span) if span else (None, None)
    return Chunk(
        chunk_id=chunk_id,
        unit_id=chunk_id.split("#")[0],
        work_id=work_id,
        edition_id=edition_id,
        lang=edition_id.split(".")[1],
        divisions=divisions or [],
        seq=0,
        bekker_start=start,
        bekker_end=end,
        bekker_precision="interpolated" if span else "none",
        text=text,
        embed_text=text,
    )


def hits(*chunks: Chunk, start_score: float = 0.9) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk=c, score=round(start_score - i * 0.01, 4), rank=i, method="dense")
        for i, c in enumerate(chunks, start=1)
    ]


def citation(n: int, c: Chunk, *, authenticity: str = "genuine") -> Citation:
    return Citation(
        n=n,
        chunk_id=c.chunk_id,
        work_id=c.work_id,
        work_title=c.work_id,
        edition_id=c.edition_id,
        translator="T",
        lang=c.lang,
        locator=bekker.format_range(c.bekker_start, c.bekker_end) or "s/n",
        bekker_start=c.bekker_start,
        bekker_end=c.bekker_end,
        authenticity=authenticity,
        licence="CC0",
        source_url="https://example.org",
        text=c.text,
    )


def bspan(start: str, end: str, work_id: str = "eth-nic") -> dict:
    return {"work_id": work_id, "bekker_start": start, "bekker_end": end}


# corpus_version the eval tests run with; reviewed cases were checked against it.
CORPUS_VERSION = "cv-test"


def case(id: str, **overrides) -> dict:
    data = {
        "id": id,
        "question": f"Pregunta de prueba {id}",
        "question_lang": "es",
        "type": "lookup",
        "expected_behavior": "answer",
        "evidence": [[bspan("1106b36", "1107a2")]],
        "key_points": ["término medio|justo medio", "virtud"],
        "split": "dev",
        "source": "handwritten",
        "revisado_por": "persona",
        "fecha_revision": "2026-11-16",
        "corpus_version_checked": CORPUS_VERSION,
    }
    data.update(overrides)
    return data


def abstain_case(id: str, **overrides) -> dict:
    data = case(
        id,
        type="unanswerable",
        unanswerable_reason="out_of_domain",
        expected_behavior="abstain",
        evidence=[],
        key_points=[],
    )
    data.update(overrides)
    return data


def write_dataset(directory: Path, cases: list[dict], name: str = "cases.jsonl") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), "utf-8")
    return path
