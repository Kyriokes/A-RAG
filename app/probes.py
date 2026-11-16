"""Live checks of the model providers for GET /readyz (PLAN, Etapa 0: "/readyz devuelve
200 con base, embedding y LLM en estado correcto").

Checking the configuration alone lets a dead endpoint, a rejected key or a retired model
id pass. These probes call the providers, cheaply, and keep the result for
settings.readyz_probe_ttl_s seconds so frequent pings do not spend quota:

- embeddings: one short embedding, whose length must be settings.embedding_dim;
- LLM: GET {llm_base_url}/models, which must list settings.llm_model (costs no tokens).
  A provider that does not list models gives a warning, not a problem.

Fake providers are not probed. /readyz is public, so problems name what failed and the
provider's own message goes only to the log.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from app.embeddings import Embedder, get_embedder
from app.settings import Settings

log = logging.getLogger(__name__)

PROBE_TEXT = "Aristóteles"


@dataclass
class ProbeResult:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def probe_embeddings(
    settings: Settings, factory: Callable[[Settings], Embedder] = get_embedder
) -> ProbeResult:
    if settings.embedding_provider == "fake":
        return ProbeResult()
    quick = settings.model_copy(
        update={"embedding_max_retries": 0, "embedding_timeout_s": settings.readyz_probe_timeout_s}
    )
    try:
        vector = factory(quick).embed_query(PROBE_TEXT)
    except Exception as exc:  # any failure of the provider is the answer we want
        log.error("readyz: la prueba de embeddings falló: %s", exc)
        return ProbeResult(problems=["embeddings: el proveedor no respondió a la prueba"])
    if len(vector) != settings.embedding_dim:
        return ProbeResult(
            problems=[
                f"embeddings: el proveedor devuelve {len(vector)} dimensiones y la "
                f"configuración espera {settings.embedding_dim}"
            ]
        )
    return ProbeResult()


def probe_llm(settings: Settings, transport: httpx.BaseTransport | None = None) -> ProbeResult:
    if settings.llm_provider == "fake":
        return ProbeResult()
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    url = settings.llm_base_url.rstrip("/") + "/models"
    try:
        with httpx.Client(
            timeout=settings.readyz_probe_timeout_s, headers=headers, transport=transport
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        log.error("readyz: la prueba del LLM falló: %s", exc)
        return ProbeResult(problems=["LLM: el proveedor no respondió a la prueba"])
    if response.status_code in (404, 405, 501):
        return ProbeResult(
            warnings=[
                f"LLM: el proveedor no lista modelos (HTTP {response.status_code}); no se "
                f"pudo comprobar que ofrezca {settings.llm_model}"
            ]
        )
    if response.status_code >= 400:
        log.error("readyz: GET /models devolvió %s: %s", response.status_code, response.text[:300])
        status = response.status_code
        return ProbeResult(problems=[f"LLM: el proveedor rechazó la prueba (HTTP {status})"])
    try:
        ids = {str(m.get("id")) for m in response.json().get("data", [])}
    except (ValueError, AttributeError):
        return ProbeResult(warnings=["LLM: la lista de modelos del proveedor no se pudo leer"])
    if settings.llm_model not in ids:
        return ProbeResult(problems=[f"LLM: el proveedor no ofrece el modelo {settings.llm_model}"])
    return ProbeResult()


class ProviderProbe:
    """probe_embeddings + probe_llm, cached for settings.readyz_probe_ttl_s seconds (per
    process, thread-safe; a change of provider settings probes again)."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        embedder_factory: Callable[[Settings], Embedder] = get_embedder,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._clock = clock
        self._factory = embedder_factory
        self._transport = transport
        self._lock = threading.Lock()
        self._cached: tuple[tuple, float, ProbeResult] | None = None

    def __call__(self, settings: Settings) -> ProbeResult:
        key = (
            settings.embedding_provider,
            settings.embedding_base_url,
            settings.embedding_provider_model,
            settings.embedding_dim,
            settings.llm_provider,
            settings.llm_base_url,
            settings.llm_model,
        )
        now = self._clock()
        with self._lock:
            if (
                self._cached is not None
                and self._cached[0] == key
                and now - self._cached[1] < settings.readyz_probe_ttl_s
            ):
                return self._cached[2]
            embedding = probe_embeddings(settings, self._factory)
            llm = probe_llm(settings, self._transport)
            result = ProbeResult(
                problems=embedding.problems + llm.problems,
                warnings=embedding.warnings + llm.warnings,
            )
            self._cached = (key, now, result)
            return result
