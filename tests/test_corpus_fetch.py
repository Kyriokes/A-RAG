"""Tests for app/corpus/fetch.py with a mocked HTTP transport (no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from app.corpus import catalog
from app.corpus.fetch import ChecksumError, fetch
from app.settings import Settings

BODY = b"<TEI>some text</TEI>"
DIGEST = hashlib.sha256(BODY).hexdigest()

SOURCES = """editions:
  # comment kept
  - edition_id: w.en.t
    work_id: w
    lang: en
    translator: T
    url: https://example.org/w.xml
    pinned_version: x
    sha256: {sha}
    licence: CC BY-SA 4.0
    licence_evidence: x
    attribution: x
    adapter: perseus_tei
    tier: 0
    has_bekker: true
    status: indexed
"""

WORKS = """works:
  - work_id: w
    abbr: W
    title_es: W
    title_en: W
    title_la: W
    authenticity: genuine
    structure: chapter
"""


def setup(tmp_path: Path, sha: str = "null") -> Settings:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "works.yaml").write_text(WORKS, encoding="utf-8")
    (corpus / "sources.yaml").write_text(SOURCES.format(sha=sha), encoding="utf-8")
    return Settings(corpus_dir=corpus, data_dir=tmp_path / "data")


def client(body: bytes, calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def edition(settings: Settings):
    return catalog.load_sources(settings)[0]


def test_first_download_records_the_sha256(tmp_path):
    settings = setup(tmp_path)
    calls: list[str] = []
    path = fetch(edition(settings), settings, client=client(BODY, calls))
    assert path.read_bytes() == BODY and calls == ["https://example.org/w.xml"]
    assert edition(settings).sha256 == DIGEST
    assert "# comment kept" in catalog.sources_path(settings).read_text(encoding="utf-8")


def test_cached_and_matching_is_not_downloaded_again(tmp_path):
    settings = setup(tmp_path, sha=DIGEST)
    path = catalog.raw_path(edition(settings), settings)
    path.parent.mkdir(parents=True)
    path.write_bytes(BODY)
    calls: list[str] = []
    assert fetch(edition(settings), settings, client=client(BODY, calls)) == path
    assert calls == []


def test_mismatch_fails_loudly_and_writes_nothing(tmp_path):
    settings = setup(tmp_path, sha=DIGEST)
    calls: list[str] = []
    with pytest.raises(ChecksumError, match="sha256 no coincide"):
        fetch(edition(settings), settings, client=client(b"changed upstream", calls))
    assert not catalog.raw_path(edition(settings), settings).exists()


def test_corrupt_cache_is_replaced_by_a_matching_download(tmp_path):
    settings = setup(tmp_path, sha=DIGEST)
    path = catalog.raw_path(edition(settings), settings)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"truncated")
    calls: list[str] = []
    fetch(edition(settings), settings, client=client(BODY, calls))
    assert path.read_bytes() == BODY and len(calls) == 1
