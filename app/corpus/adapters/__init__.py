"""Source adapters: one per source format, selected by the manifest's `adapter` column.

Each adapter exposes ``adapt(raw_path, edition, work, reference_path=None, settings=None)``
returning an object with ``units``, ``warnings``, ``header`` and ``level_types``.
"""

from __future__ import annotations

from app.corpus.adapters import perseus_tei

ADAPTERS = {
    "perseus_tei": perseus_tei.adapt,
}
