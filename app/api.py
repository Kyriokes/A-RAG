"""HTTP API (PLAN, Etapa 4): search, ask, passages, works, health and readiness.

Every outside dependency (settings, database connection, index functions, LLM,
catalogue) arrives through a FastAPI dependency, so tests swap them with
`app.dependency_overrides`. Run locally with `make dev`.

Chunk ids contain "#" ("eth-nic.en.rackham:2.6#1"): clients must percent-encode it in
GET /passages/{chunk_id} ("%23").

At startup (when served) the app refuses to run on fake models in production and on an
index whose embedding model or dimension differs from the configuration (rule 4); an
unreachable database is only logged, and /readyz reports it.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import cache, embeddings, limits, retrieval
from app.cache import AnswerCache
from app.citations import Catalogue, build_citation, load_catalogue
from app.db import connect
from app.embeddings import EmbeddingError
from app.generation import answer, load_prompt
from app.limits import DailyUsage, MinuteLimiter
from app.llm import FAKE_MODEL_NAME, LLMError, config_problems, get_llm
from app.logging_setup import log_query, question_fields, retrieved_fields, setup_logging
from app.models import AskResult, Chunk, Citation, RetrievedChunk, SourceStatus
from app.probes import ProbeResult, ProviderProbe
from app.query_parse import detect_lang
from app.retrieval import IndexCheck, IndexNotReadyError
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)

AnswerLang = Literal["es", "en"]


# --- dependencies ------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexAccess:
    """The index functions the endpoints use; tests replace them with in-memory ones."""

    retrieve: Callable[..., list[RetrievedChunk]] = retrieval.retrieve
    get_chunk: Callable[..., Chunk | None] = retrieval.get_chunk
    neighbours: Callable[..., list[Chunk]] = retrieval.neighbours
    version: Callable[..., str] = retrieval.current_index_version
    check: Callable[..., IndexCheck] = retrieval.check_active_index


def get_app_settings() -> Settings:
    return get_settings()


def get_catalogue() -> Catalogue:
    return load_catalogue()


def get_index_access() -> IndexAccess:
    return IndexAccess()


def _connect_autocommit(settings: Settings) -> psycopg.Connection:
    return connect(settings, autocommit=True)


def get_connector() -> Callable[[Settings], psycopg.Connection]:
    return _connect_autocommit


def get_conn(
    settings: Annotated[Settings, Depends(get_app_settings)],
    connector: Annotated[Callable[[Settings], psycopg.Connection], Depends(get_connector)],
) -> Iterator[psycopg.Connection]:
    try:
        conn = connector(settings)
    except psycopg.Error as exc:
        log.error("No se pudo conectar a la base: %s", exc)
        raise HTTPException(503, detail="Base de datos no disponible.") from exc
    try:
        yield conn
    finally:
        conn.close()


@lru_cache(maxsize=1)
def _default_llm() -> Any:
    return get_llm(get_settings())


@lru_cache(maxsize=1)
def _default_probe() -> ProviderProbe:
    return ProviderProbe()


def get_probe() -> Callable[[Settings], ProbeResult]:
    """Live provider checks for /readyz (app.probes); tests replace them."""
    return _default_probe()


def get_llm_client() -> Any:
    """The configured LLM, or None when its configuration is broken: /ask then answers in
    search-only mode with the error, instead of failing."""
    try:
        return _default_llm()
    except LLMError as exc:
        log.error("LLM mal configurado: %s", exc)
        return None


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CatalogueDep = Annotated[Catalogue, Depends(get_catalogue)]
IndexDep = Annotated[IndexAccess, Depends(get_index_access)]
ConnDep = Annotated[Any, Depends(get_conn)]
ConnectorDep = Annotated[Callable[[Settings], Any], Depends(get_connector)]
LLMDep = Annotated[Any, Depends(get_llm_client)]
ProbeDep = Annotated[Callable[[Settings], ProbeResult], Depends(get_probe)]


def get_minute_limiter(request: Request) -> MinuteLimiter:
    """The app's in-memory per-minute limiter (one per process)."""
    return request.app.state.minute_limiter


def get_usage(conn: ConnDep, settings: SettingsDep) -> DailyUsage | None:
    """Per-day request counters and the token budget, in Postgres. None only when a test
    stubs the connection out (get_conn yields None): the per-day parts are then skipped."""
    if conn is None:
        return None
    return DailyUsage(conn, settings.rate_limit_retention_days, settings.client_key_secret)


def get_answer_cache(conn: ConnDep, settings: SettingsDep) -> AnswerCache | None:
    """The exact answer cache in Postgres; None when the connection is stubbed out."""
    return AnswerCache(conn, settings.answer_cache_ttl_days) if conn is not None else None


MinuteLimiterDep = Annotated[MinuteLimiter, Depends(get_minute_limiter)]
UsageDep = Annotated[DailyUsage | None, Depends(get_usage)]
AnswerCacheDep = Annotated[AnswerCache | None, Depends(get_answer_cache)]


# --- request and response models ---------------------------------------------------------


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    works: list[str] | None = None
    lang: AnswerLang | None = None  # passage language filter
    k: int | None = Field(default=None, ge=1)  # capped at settings.search_max_k
    include_dubious: bool = True


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)  # capped at settings.question_max_chars
    works: list[str] | None = None
    lang: AnswerLang | None = None  # passage language filter; the answer follows the question
    include_dubious: bool = True


class SearchHit(BaseModel):
    rank: int
    score: float
    method: str
    citation: Citation


class SearchResponse(BaseModel):
    query: str
    query_lang: str
    index_version: str
    results: list[SearchHit]


class RetrievedRef(BaseModel):
    chunk_id: str
    work_id: str
    rank: int
    score: float
    method: str


class AskResponse(BaseModel):
    """AskResult for the public: retrieved chunks by reference, and the error as a code
    ("llm_unavailable", "llm_error", "invalid_json", "budget_exhausted") without the
    provider's message. `cached`: served from the answer cache; tokens and latency are then
    those of this request (no tokens)."""

    answer: str
    answer_lang: str
    abstained: bool
    abstain_reason: str | None = None
    search_only: bool = False
    citations: list[Citation]
    retrieved: list[RetrievedRef]
    index_version: str
    prompt_version: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    latency_ms: dict[str, float]
    error: str | None = None
    cached: bool = False

    @classmethod
    def from_result(cls, r: AskResult) -> AskResponse:
        return cls(
            **r.model_dump(exclude={"retrieved", "error", "citations"}),
            citations=r.citations,
            retrieved=[
                RetrievedRef(
                    chunk_id=h.chunk.chunk_id,
                    work_id=h.chunk.work_id,
                    rank=h.rank,
                    score=h.score,
                    method=h.method,
                )
                for h in r.retrieved
            ],
            error=r.error.split(":", 1)[0] if r.error else None,
        )


class Passage(BaseModel):
    chunk_id: str
    work_id: str
    work_title: str
    edition_id: str
    translator: str | None
    lang: str
    locator: str
    divisions: list[tuple[str, str]]
    bekker_start: int | None
    bekker_end: int | None
    authenticity: str
    licence: str
    source_url: str
    text: str


class PassageResponse(BaseModel):
    passage: Passage
    before: list[Passage]
    after: list[Passage]


class EditionInfo(BaseModel):
    edition_id: str
    lang: str
    translator: str | None
    pub_year: int | None
    licence: str
    attribution: str
    source_url: str
    has_bekker: bool


class WorkInfo(BaseModel):
    work_id: str
    abbr: str
    title_es: str
    title_en: str
    title_la: str
    authenticity: str
    structure: str
    bekker_range: str | None
    languages: list[str]  # languages with an indexed edition
    editions: list[EditionInfo]  # indexed editions only


# --- validation helpers ------------------------------------------------------------------


def _invalid(where: str, field: str, msg: str) -> HTTPException:
    return HTTPException(422, detail=[{"loc": [where, field], "msg": msg, "type": "value_error"}])


def _check_text(text: str, field: str, settings: Settings) -> str:
    text = text.strip()
    if not text:
        raise _invalid("body", field, "No puede estar vacío.")
    if len(text) > settings.question_max_chars:
        raise _invalid("body", field, f"Máximo {settings.question_max_chars} caracteres.")
    return text


def _check_works(works: list[str] | None, catalogue: Catalogue) -> list[str] | None:
    """The requested works, deduplicated; 422 for unknown ids or more ids than the
    catalogue has (so the message stays short)."""
    if not works:
        return None
    valid = ", ".join(catalogue.works)
    if len(works) > len(catalogue.works):
        raise _invalid("body", "works", f"Más obras que las del catálogo. Válidas: {valid}.")
    unknown = sorted(set(works) - set(catalogue.works))
    if unknown:
        shown = ", ".join(w[:40] for w in unknown)
        raise _invalid("body", "works", f"Obras desconocidas: {shown}. Válidas: {valid}.")
    return list(dict.fromkeys(works))


def _passage(chunk: Chunk, catalogue: Catalogue, lang: str) -> Passage:
    citation = build_citation(0, chunk, catalogue.works, catalogue.editions, lang=lang)
    return Passage(**citation.model_dump(exclude={"n"}), divisions=chunk.divisions)


def _ask_log_fields(out: AskResponse, error_detail: str | None) -> dict[str, Any]:
    """Query-log fields of an /ask response. The log keeps the full error message (it is
    not public), cut to 300 characters."""
    error = error_detail or out.error
    return {
        "status": 200,
        "lang": out.answer_lang,
        "retrieved": retrieved_fields(out.retrieved),
        "citations": [c.chunk_id for c in out.citations],
        "index_version": out.index_version,
        "prompt_version": out.prompt_version,
        "model": out.model,
        "provider": out.provider,
        "tokens_in": out.tokens_in,
        "tokens_out": out.tokens_out,
        "latency_ms": out.latency_ms,
        "abstained": out.abstained,
        "abstain_reason": out.abstain_reason,
        "search_only": out.search_only,
        "error": error[:300] if error else None,
    }


# --- app ---------------------------------------------------------------------------------


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)


class StartupError(RuntimeError):
    """The service must not serve with this configuration."""


def check_startup(settings: Settings) -> None:
    """Raise when the service must not start: fake models in production, or an index that
    is missing or does not match the configured embedding model and dimension (rule 4).
    A database that cannot be reached is only logged (readyz reports it)."""
    if settings.env == "prod":
        fake = [
            name
            for name, provider in (
                ("EMBEDDING_PROVIDER", settings.embedding_provider),
                ("LLM_PROVIDER", settings.llm_provider),
            )
            if provider == "fake"
        ]
        if fake:
            raise StartupError(f"{' y '.join(fake)}=fake en producción: no se sirve.")
    try:
        conn = connect(settings, autocommit=True)
    except psycopg.Error as exc:
        log.error("Arranque sin base de datos (se reintenta en cada consulta): %s", exc)
        return
    with conn:
        check = retrieval.ensure_index_ready(conn, settings)
    log.info("Índice %s listo (%s)", check.index_version, check.embedding_model)


class _BodyTooLarge(StarletteHTTPException):
    def __init__(self, limit: int) -> None:
        super().__init__(413, detail=f"El cuerpo de la consulta supera {limit} bytes.")


class BodySizeLimit:
    """Refuse request bodies larger than `max_bytes` (413) without reading them whole: by
    Content-Length when it is sent, else while the body streams in."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            response = JSONResponse({"detail": _BodyTooLarge(self.max_bytes).detail}, 413)
            await response(scope, receive, send)
            return
        received = 0

        async def limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge(self.max_bytes)  # FastAPI turns it into the 413
            return message

        await self.app(scope, limited, send)


def _rate_limited(exc: limits.RateLimited, request_id: str) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.message, "limit": exc.scope, "retry_after": exc.retry_after},
        status_code=429,
        headers={"Retry-After": str(exc.retry_after), "X-Request-ID": request_id},
    )


def create_app(
    settings: Settings | None = None,
    *,
    startup_check: Callable[[Settings], None] | None = check_startup,
) -> FastAPI:
    """`startup_check` runs when the app is served (lifespan) and may refuse to start;
    None skips it (tests)."""
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Only when served (uvicorn runs the lifespan): tests keep pytest's logging.
        setup_logging(s.log_level)
        for warning in limits.config_warnings(s):
            log.warning("Configuración: %s", warning)
        if startup_check is not None:
            startup_check(s)  # an exception here makes uvicorn exit
        yield

    app = FastAPI(title="Aristóteles RAG", version="0.1.0", lifespan=lifespan)
    app.state.minute_limiter = MinuteLimiter()
    app.add_middleware(BodySizeLimit, max_bytes=s.request_max_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Eval-Key"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )

    @app.exception_handler(IndexNotReadyError)
    def _index_not_ready(request: Request, exc: IndexNotReadyError) -> JSONResponse:
        log.error("Índice no disponible: %s", exc)
        return JSONResponse({"detail": "El índice no está listo."}, status_code=503)

    @app.exception_handler(EmbeddingError)
    def _embedding_down(request: Request, exc: EmbeddingError) -> JSONResponse:
        log.error("Embeddings no disponibles: %s", exc)
        return JSONResponse(
            {"detail": "El servicio de embeddings no está disponible."}, status_code=503
        )

    @app.exception_handler(psycopg.Error)
    def _db_error(request: Request, exc: psycopg.Error) -> JSONResponse:
        log.error("Error de base de datos: %s", exc)
        return JSONResponse({"detail": "Base de datos no disponible."}, status_code=503)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(
        settings: SettingsDep, connector: ConnectorDep, index: IndexDep, probe: ProbeDep
    ) -> JSONResponse:
        problems: list[str] = []
        warnings: list[str] = []
        body: dict[str, Any] = {"database": False, "index": None}
        try:
            conn = connector(settings)
        except psycopg.Error as exc:
            log.error("readyz: sin base de datos: %s", exc)
            problems.append("base de datos no disponible")
        else:
            table_problems: list[str] = []
            try:
                body["database"] = True
                check = index.check(conn, settings)
                # Only a real connection can be inspected (tests pass stand-ins).
                if isinstance(conn, psycopg.Connection):
                    table_problems = limits.missing_tables(conn)
            finally:
                conn.close()
            body["index"] = {
                "index_version": check.index_version,
                "embedding_model": check.embedding_model,
                "embedding_provider": check.embedding_provider,
                "embedding_dim": check.embedding_dim,
                "chunk_count": check.chunk_count,
            }
            problems += check.problems + table_problems
            warnings += check.warnings
        embedding_problems = embeddings.config_problems(settings)
        problems += embedding_problems
        body["embedding"] = {
            "provider": settings.embedding_provider,
            "model": embeddings.expected_model_name(settings),
            "configured": not embedding_problems,
        }
        llm_problems = config_problems(settings)
        problems += llm_problems
        if not embedding_problems and not llm_problems:
            live = probe(settings)  # calls the providers; cached (app.probes)
            problems += live.problems
            warnings += live.warnings
        warnings += limits.config_warnings(settings)
        fake = settings.llm_provider == "fake"
        body["llm"] = {
            "provider": settings.llm_provider,
            "model": FAKE_MODEL_NAME if fake else settings.llm_model,
            "fallback": bool(settings.llm_fallback_base_url and settings.llm_fallback_model),
            "configured": not llm_problems,
        }
        body.update(ready=not problems, problems=problems, warnings=warnings)
        return JSONResponse(body, status_code=200 if not problems else 503)

    @app.get("/works", response_model=list[WorkInfo])
    def works(catalogue: CatalogueDep) -> list[WorkInfo]:
        out = []
        for work in catalogue.works.values():
            editions = [
                EditionInfo(
                    edition_id=e.edition_id,
                    lang=e.lang.value,
                    translator=e.translator,
                    pub_year=e.pub_year,
                    licence=e.licence,
                    attribution=e.attribution,
                    source_url=e.url,
                    has_bekker=e.has_bekker,
                )
                for e in catalogue.editions.values()
                if e.work_id == work.work_id and e.status == SourceStatus.indexed
            ]
            out.append(
                WorkInfo(
                    work_id=work.work_id,
                    abbr=work.abbr,
                    title_es=work.title_es,
                    title_en=work.title_en,
                    title_la=work.title_la,
                    authenticity=work.authenticity.value,
                    structure=work.structure.value,
                    bekker_range=(
                        f"{work.bekker_start}–{work.bekker_end}" if work.bekker_start else None
                    ),
                    languages=sorted({e.lang for e in editions}),
                    editions=editions,
                )
            )
        return out

    @app.post("/search", response_model=SearchResponse)
    def search(
        req: SearchRequest,
        request: Request,
        response: Response,
        settings: SettingsDep,
        catalogue: CatalogueDep,
        index: IndexDep,
        conn: ConnDep,
        usage: UsageDep,
        minute: MinuteLimiterDep,
    ) -> Any:
        """Per-IP limits of their own (429; eval runs skip them), then retrieval."""
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        response.headers["X-Request-ID"] = request_id
        query = _check_text(req.query, "query", settings)
        if req.k is not None and req.k > settings.search_max_k:
            raise _invalid("body", "k", f"Máximo {settings.search_max_k}.")
        works = _check_works(req.works, catalogue)
        if not limits.is_eval_request(request.headers.get("x-eval-key"), settings):
            now = datetime.now(UTC)
            ip = limits.request_ip(request, settings)
            try:
                limits.enforce(
                    ip,
                    minute=minute,
                    usage=usage,
                    settings=settings,
                    now=now,
                    endpoint=limits.SEARCH,
                )
            except limits.RateLimited as exc:
                log_query(
                    {
                        "request_id": request_id,
                        "endpoint": "/search",
                        "status": 429,
                        "client": limits.log_client_id(ip, now.date(), settings),
                        "rate_limited": exc.scope,
                        "latency_ms": {"total": _ms(started)},
                    }
                )
                return _rate_limited(exc, request_id)
        version = index.version(conn)
        k = req.k or settings.retrieval_k
        t = time.perf_counter()
        hits = index.retrieve(
            query,
            k=k,
            works=works,
            lang=req.lang,
            include_dubious=req.include_dubious,
            conn=conn,
        )
        retrieve_ms = _ms(t)
        lang = detect_lang(query)
        log_query(
            {
                "request_id": request_id,
                "endpoint": "/search",
                "status": 200,
                **question_fields(query, settings),
                "lang": lang,
                "filters": {
                    "works": works,
                    "lang": req.lang,
                    "include_dubious": req.include_dubious,
                    "k": k,
                },
                "retrieved": retrieved_fields(hits),
                "index_version": version,
                "latency_ms": {"retrieve": retrieve_ms, "total": _ms(started)},
            }
        )
        return SearchResponse(
            query=query,
            query_lang=lang,
            index_version=version,
            results=[
                SearchHit(
                    rank=h.rank,
                    score=h.score,
                    method=h.method,
                    citation=build_citation(
                        h.rank, h.chunk, catalogue.works, catalogue.editions, lang=lang
                    ),
                )
                for h in hits
            ],
        )

    @app.post("/ask", response_model=AskResponse)
    def ask(
        req: AskRequest,
        request: Request,
        response: Response,
        settings: SettingsDep,
        catalogue: CatalogueDep,
        index: IndexDep,
        llm: LLMDep,
        conn: ConnDep,
        usage: UsageDep,
        answers: AnswerCacheDep,
        minute: MinuteLimiterDep,
    ) -> Any:
        """Per-IP limits (429), then the answer cache, then generation, in search-only mode
        once the daily token budget is spent. Eval runs (X-Eval-Key) skip the limits, the
        budget and the cache. Writes one JSON log line (app.logging_setup)."""
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        response.headers["X-Request-ID"] = request_id
        question = _check_text(req.question, "question", settings)
        works = _check_works(req.works, catalogue)
        now = datetime.now(UTC)
        eval_run = limits.is_eval_request(request.headers.get("x-eval-key"), settings)
        ip = limits.request_ip(request, settings)
        entry: dict[str, Any] = {
            "request_id": request_id,
            "endpoint": "/ask",
            "eval": eval_run,
            "client": limits.log_client_id(ip, now.date(), settings),
            **question_fields(question, settings),
            "filters": {"works": works, "lang": req.lang, "include_dubious": req.include_dubious},
        }
        stages: dict[str, float] = {}

        if not eval_run:
            t = time.perf_counter()
            try:
                limits.enforce(ip, minute=minute, usage=usage, settings=settings, now=now)
            except limits.RateLimited as exc:
                entry.update(status=429, rate_limited=exc.scope, latency_ms={"total": _ms(started)})
                log_query(entry)
                return _rate_limited(exc, request_id)
            stages["limits"] = _ms(t)

        index_version = index.version(conn)
        key = None
        if answers is not None and llm is not None and not eval_run:
            t = time.perf_counter()
            key = cache.make_key(
                question,
                answer_lang=detect_lang(question),
                index_version=index_version,
                model=getattr(llm, "model", "") or "",
                prompt_version=load_prompt()[1],
                works=works,
                lang=req.lang,
                include_dubious=req.include_dubious,
                settings=settings,
            )
            stored = answers.get(key)
            stages["cache"] = _ms(t)
            if stored is not None:
                try:
                    out = AskResponse.model_validate(
                        {
                            **stored,
                            "cached": True,
                            "tokens_in": 0,
                            "tokens_out": 0,
                            "latency_ms": {"cache": stages["cache"], "total": _ms(started)},
                        }
                    )
                except ValidationError:
                    log.warning("Respuesta en caché con otro esquema: se vuelve a generar")
                else:
                    log_query({**entry, **_ask_log_fields(out, None), "cache": "hit"})
                    return out

        # Public requests reserve their tokens from the daily budget before the LLM call
        # (atomic: concurrent requests cannot overshoot it) and settle the real count after.
        reserved = (
            0 if eval_run or llm is None else limits.reserve_budget(usage, settings, now.date())
        )
        try:
            result = answer(
                question,
                works=works,
                lang=req.lang,
                include_dubious=req.include_dubious,
                retriever=index.retrieve,
                llm=llm,
                conn=conn,
                settings=settings,
                catalogue=catalogue,
                index_version=index_version,
                skip_llm="budget_exhausted: se agotó el presupuesto diario de tokens"
                if reserved is None
                else None,
            )
        except BaseException:
            if reserved and usage is not None:
                usage.settle_tokens(now.date(), limits.PUBLIC, reserved, 0)
            raise
        tokens = result.tokens_in + result.tokens_out
        if usage is not None and reserved:
            usage.settle_tokens(now.date(), limits.PUBLIC, reserved, tokens)
        elif usage is not None and tokens > 0:
            usage.add_tokens(now.date(), limits.EVAL if eval_run else limits.PUBLIC, tokens)
        out = AskResponse.from_result(result)
        cache_state = "skip" if key is None else "miss"
        if (
            key is not None
            and answers is not None
            and cache.cacheable(result, key.model)
            and answers.put(key, out.model_dump(mode="json"))
        ):
            cache_state = "stored"
        fields = _ask_log_fields(out, result.error)
        fields["latency_ms"] = {**stages, **out.latency_ms, "request": _ms(started)}
        log_query({**entry, **fields, "cache": cache_state})
        return out

    @app.get("/passages/{chunk_id}", response_model=PassageResponse)
    def passage(
        chunk_id: str,
        settings: SettingsDep,
        catalogue: CatalogueDep,
        index: IndexDep,
        conn: ConnDep,
        context: Annotated[int, Query(ge=0)] = 1,
        lang: AnswerLang = "es",
    ) -> PassageResponse:
        if context > settings.passages_max_context:
            raise _invalid("query", "context", f"Máximo {settings.passages_max_context}.")
        chunk = index.get_chunk(chunk_id, conn=conn)
        if chunk is None:
            raise HTTPException(404, detail="No existe ese pasaje en el índice activo.")
        around = index.neighbours(chunk_id, conn=conn, window=context) if context else []
        return PassageResponse(
            passage=_passage(chunk, catalogue, lang),
            before=[_passage(c, catalogue, lang) for c in around if c.seq < chunk.seq],
            after=[_passage(c, catalogue, lang) for c in around if c.seq > chunk.seq],
        )

    return app


app = create_app()
