"""Download sources to data/raw/ with a cache and a pinned sha256.

On the first download of an edition whose manifest row has no ``sha256``, the digest is
computed and written into corpus/sources.yaml. From then on it is enforced: a download
whose digest differs fails loudly, and a cached file that matches is never downloaded again.

    python -m app.corpus.fetch [--all] [EDITION_ID ...]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

from app.corpus import catalog
from app.models import Edition, SourceStatus
from app.settings import Settings, get_settings

TIMEOUT_S = 60.0


class ChecksumError(RuntimeError):
    """The downloaded or cached file does not match the sha256 pinned in the manifest."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _download(url: str, client: httpx.Client | None) -> bytes:
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=True)
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.content
    finally:
        if own:
            client.close()


def record_sha256(manifest: Path, edition_id: str, digest: str) -> None:
    """Write `sha256: <digest>` into the edition's row of sources.yaml, keeping the rest."""
    catalog.set_row_field(manifest, "edition_id", edition_id, "sha256", digest)


def fetch(
    edition: Edition,
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
    record: bool = True,
) -> Path:
    """Return the local copy of an edition's source, downloading it only when needed.

    Raises ChecksumError when the pinned sha256 does not match what was downloaded.
    """
    settings = settings or get_settings()
    path = catalog.raw_path(edition, settings)
    expected = edition.sha256

    if path.exists():
        cached = sha256_file(path)
        if expected is None:
            if record:
                record_sha256(catalog.sources_path(settings), edition.edition_id, cached)
            return path
        if cached == expected:
            return path
        # A cached file that does not match (truncated or stale): download it again below.

    data = _download(edition.url, client)
    digest = hashlib.sha256(data).hexdigest()
    if expected is not None and digest != expected:
        raise ChecksumError(
            f"{edition.edition_id}: sha256 no coincide.\n"
            f"  manifiesto: {expected}\n  descargado: {digest}\n  url: {edition.url}\n"
            "La fuente cambió o la descarga está corrupta. No se usa el archivo."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    if expected is None and record:
        record_sha256(catalog.sources_path(settings), edition.edition_id, digest)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Descarga las fuentes del manifiesto.")
    parser.add_argument("edition_ids", nargs="*", help="ediciones a descargar")
    parser.add_argument("--all", action="store_true", help="todas salvo las excluidas")
    args = parser.parse_args(argv)
    editions = catalog.load_sources()
    if args.edition_ids:
        wanted = set(args.edition_ids)
        editions = [e for e in editions if e.edition_id in wanted]
    elif not args.all:
        editions = [
            e for e in editions if e.status in (SourceStatus.indexed, SourceStatus.reference)
        ]
    editions = [e for e in editions if e.status != SourceStatus.excluded]
    try:
        for edition in editions:
            path = fetch(edition)
            print(f"{edition.edition_id}: {path} ({sha256_file(path)[:12]})")
    except (ChecksumError, httpx.HTTPError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
