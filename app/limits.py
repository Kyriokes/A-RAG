"""Per-IP request limits and the global daily token budget (PLAN, Etapa 7).

POST /ask has the limits below; POST /search has its own pair
(settings.search_rate_limit_per_minute and _per_day), counted apart.

- Per minute (settings.rate_limit_per_minute): in memory, sliding window. It is per
  process, so the service runs one worker.
- Per day (settings.rate_limit_per_day): in Postgres, table rate_limit_daily, keyed by an
  HMAC (key settings.client_key_secret) of the UTC day and the client address. Neither the
  address nor anything that maps back to it without the key is stored or logged.
- Token budget (settings.daily_token_budget): LLM tokens used today (UTC) by public
  requests, in Postgres, table token_usage_daily. Each /ask reserves an upper estimate of
  its tokens before calling the LLM, in one atomic statement, and settles the real count
  afterwards; when the reservation does not fit, it answers in search-only mode. So
  concurrent requests cannot all pass a check and overshoot the budget together.

A limit of 0 or less is switched off.

Requests whose X-Eval-Key header equals settings.eval_key (non-empty) are eval runs: they
skip the per-IP limits and the budget, and their tokens are recorded apart (source
"eval"). The budget is set below the provider's daily quota precisely to leave room for
the scheduled evals (PLAN, "Costos y topes").

The client address is the TCP peer, unless the peer is listed in
settings.forwarded_allow_ips (the reverse proxy in front of the service). Then it is the
first address of X-Forwarded-For that is not a trusted proxy, reading from the right,
because each proxy appends the address it saw and only the right end is trustworthy. With
"*" every peer is trusted and the leftmost address is used, which the client can forge.
This is what uvicorn's --proxy-headers does with the same FORWARDED_ALLOW_IPS variable, so
the app and the server agree whether or not the server already rewrote the peer.

The limits count an IPv6 client by its /settings.rate_limit_ipv6_prefix network, since one
client usually controls a whole /64 (limit_subject).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

import psycopg

from app.settings import Settings

WINDOW_S = 60.0
PUBLIC = "public"
EVAL = "eval"
TABLES = ("rate_limit_daily", "token_usage_daily", "answer_cache")  # migration 002
DEFAULT_FORWARDED_ALLOW_IPS = "127.0.0.1,::1"  # uvicorn's default
_MAX_HOST_CHARS = 100  # longer X-Forwarded-For entries are cut before use as a key
# HMAC key when settings.client_key_secret is empty: random, never stored.
_PROCESS_SECRET = secrets.token_hex(32)


# --- client address ------------------------------------------------------------------------


class TrustedProxies:
    """Parsed settings.forwarded_allow_ips: IPs, networks and literals, or "*"."""

    def __init__(self, spec: str) -> None:
        self.any = spec.strip() == "*"
        self.networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self.literals: set[str] = set()
        for item in (x.strip() for x in spec.split(",")):
            if not item:
                continue
            try:
                self.networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                self.literals.add(item)  # e.g. a unix socket path, or "testclient"

    def __contains__(self, host: str | None) -> bool:
        if self.any:
            return True
        if not host:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return host in self.literals
        # A dual-stack socket shows an IPv4 peer as "::ffff:10.0.0.5".
        mapped = getattr(address, "ipv4_mapped", None)
        return any(address in net or (mapped and mapped in net) for net in self.networks)


@lru_cache(maxsize=8)
def _trusted(spec: str) -> TrustedProxies:
    return TrustedProxies(spec)


def _host(value: str) -> str:
    """Address part of an X-Forwarded-For entry: "[::1]:80" -> "::1", "1.2.3.4:5" -> "1.2.3.4"."""
    value = value.strip()[:_MAX_HOST_CHARS]
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def client_ip(peer: str | None, forwarded_for: str | None, trusted: str) -> str:
    """The client's address, from the TCP peer and the X-Forwarded-For header (see module)."""
    if not peer:
        return "unknown"
    proxies = _trusted(trusted)
    if not forwarded_for or peer not in proxies:
        return peer
    hops = [_host(h) for h in forwarded_for.split(",") if h.strip()]
    if not hops:
        return peer
    if proxies.any:
        return hops[0]
    for host in reversed(hops):
        if host not in proxies:
            return host
    return hops[0]  # every hop is a trusted proxy: the client is one of them


def request_ip(request, settings: Settings) -> str:
    """client_ip() for a Starlette request; several X-Forwarded-For headers are joined."""
    peer = request.client.host if request.client else None
    forwarded = ", ".join(request.headers.getlist("x-forwarded-for")) or None
    return client_ip(peer, forwarded, settings.forwarded_allow_ips)


def limit_subject(ip: str, ipv6_prefix: int) -> str:
    """What the per-IP limits count: an IPv4 address (IPv4-mapped IPv6 unwrapped), or the
    IPv6 network of `ipv6_prefix` bits around the address. Anything else as is."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if address.version == 6:
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/{ipv6_prefix}", strict=False))
    return str(address)


def client_key(subject: str, day: date, secret: str) -> str:
    """What the database stores (and the logs show a prefix of) for a client on `day`:
    HMAC-SHA256 of "<day>|<subject>" with `secret`, or with a random per-process key when
    `secret` is empty. Without the key it cannot be mapped back to the address."""
    key = (secret or _PROCESS_SECRET).encode()
    return hmac.new(key, f"{day.isoformat()}|{subject}".encode(), hashlib.sha256).hexdigest()


def log_client_id(ip: str, day: date, settings: Settings) -> str:
    """Short client id for the query log (a prefix of client_key)."""
    subject = limit_subject(ip, settings.rate_limit_ipv6_prefix)
    return client_key(subject, day, settings.client_key_secret)[:12]


def is_eval_request(header: str | None, settings: Settings) -> bool:
    """True when the X-Eval-Key header matches a configured (non-empty) settings.eval_key."""
    key = settings.eval_key
    if not key or header is None:
        return False
    return hmac.compare_digest(header.encode(), key.encode())


# --- limits --------------------------------------------------------------------------------


ASK = "ask"
SEARCH = "search"


class RateLimited(Exception):
    """A per-IP limit was reached; `retry_after` is in whole seconds."""

    def __init__(self, scope: str, limit: int, retry_after: int, endpoint: str = ASK) -> None:
        super().__init__(f"{endpoint} {scope} limit {limit} reached")
        self.scope = scope  # "minute" or "day"
        self.limit = limit
        self.retry_after = retry_after
        self.endpoint = endpoint  # ASK or SEARCH

    @property
    def message(self) -> str:
        if self.endpoint == SEARCH:
            if self.scope == "minute":
                return (
                    f"Se alcanzó el límite de {self.limit} búsquedas por minuto. "
                    f"Se puede volver a buscar en {self.retry_after} segundos."
                )
            return (
                f"Se alcanzó el límite de {self.limit} búsquedas por día. Se puede volver a "
                "buscar mañana (a las 00:00 UTC)."
            )
        if self.scope == "minute":
            return (
                f"Se alcanzó el límite de {self.limit} preguntas por minuto. "
                f"Se puede volver a preguntar en {self.retry_after} segundos."
            )
        return (
            f"Se alcanzó el límite de {self.limit} preguntas por día. Se puede volver a "
            "preguntar mañana (a las 00:00 UTC). La búsqueda de pasajes sigue disponible."
        )


class MinuteLimiter:
    """Sliding one-minute window per key, in memory and thread-safe. Requests it rejects are
    not counted, so a client that keeps retrying does not extend its own wait."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = clock()

    def hit(self, key: str, limit: int) -> float | None:
        """Count one request for `key`: None when allowed, else the seconds to wait."""
        if limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            self._sweep(now)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - WINDOW_S:
                hits.popleft()
            if len(hits) >= limit:
                return max(hits[len(hits) - limit] + WINDOW_S - now, 0.0)
            hits.append(now)
            return None

    def _sweep(self, now: float) -> None:
        """Once a minute, forget the keys with no request in the last minute."""
        if now - self._last_sweep < WINDOW_S:
            return
        self._last_sweep = now
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - WINDOW_S]:
            del self._hits[key]

    def __len__(self) -> int:
        return len(self._hits)


def seconds_until_midnight(now: datetime) -> int:
    """Seconds from `now` to the next 00:00 UTC (at least 1)."""
    now = now.astimezone(UTC)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
    return max(math.ceil((midnight - now).total_seconds()), 1)


class DailyUsage:
    """Per-day counters in Postgres: requests per client and LLM tokens per source.

    Expects an autocommit connection (the API's): every method is a single statement.
    """

    def __init__(self, conn: psycopg.Connection, retention_days: int = 7, secret: str = "") -> None:
        self.conn = conn
        self.retention_days = retention_days
        self.secret = secret  # settings.client_key_secret

    def count_request(self, ip: str, day: date, limit: int) -> bool:
        """Count one request from `ip` (a limit_subject) on `day`. False, and nothing
        counted, when the client already made `limit` requests that day."""
        if limit <= 0:
            return True
        row = self.conn.execute(
            """
            INSERT INTO rate_limit_daily AS r (day, client_key, requests)
            VALUES (%s, %s, 1)
            ON CONFLICT (day, client_key) DO UPDATE SET requests = r.requests + 1
            WHERE r.requests < %s
            RETURNING requests
            """,
            (day, client_key(ip, day, self.secret), limit),
        ).fetchone()
        if row is not None and row[0] == 1:  # first request of this client today
            self.purge(day)
        return row is not None

    def purge(self, today: date) -> None:
        """Delete request counters older than retention_days."""
        if self.retention_days > 0:
            cutoff = today - timedelta(days=self.retention_days)
            self.conn.execute("DELETE FROM rate_limit_daily WHERE day < %s", (cutoff,))

    def tokens_used(self, day: date, source: str = PUBLIC) -> int:
        row = self.conn.execute(
            "SELECT tokens FROM token_usage_daily WHERE day = %s AND source = %s", (day, source)
        ).fetchone()
        return int(row[0]) if row else 0

    def add_tokens(self, day: date, source: str, tokens: int) -> None:
        self.conn.execute(
            """
            INSERT INTO token_usage_daily AS t (day, source, tokens, requests)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (day, source)
            DO UPDATE SET tokens = t.tokens + EXCLUDED.tokens, requests = t.requests + 1
            """,
            (day, source, tokens),
        )

    def reserve_tokens(self, day: date, source: str, tokens: int, budget: int) -> bool:
        """Add `tokens` to the day's count only if the total stays within `budget`, in one
        statement (concurrent reservations cannot overshoot). False when it does not fit."""
        if tokens > budget:
            return False
        row = self.conn.execute(
            """
            INSERT INTO token_usage_daily AS t (day, source, tokens, requests)
            VALUES (%(day)s, %(source)s, %(tokens)s, 0)
            ON CONFLICT (day, source) DO UPDATE SET tokens = t.tokens + EXCLUDED.tokens
            WHERE t.tokens + EXCLUDED.tokens <= %(budget)s
            RETURNING tokens
            """,
            {"day": day, "source": source, "tokens": tokens, "budget": budget},
        ).fetchone()
        return row is not None

    def settle_tokens(self, day: date, source: str, reserved: int, used: int) -> None:
        """Replace a reservation of `reserved` tokens with the `used` ones."""
        self.conn.execute(
            """
            UPDATE token_usage_daily
            SET tokens = GREATEST(tokens + %(delta)s, 0), requests = requests + %(answered)s
            WHERE day = %(day)s AND source = %(source)s
            """,
            {
                "day": day,
                "source": source,
                "delta": used - reserved,
                "answered": 1 if used > 0 else 0,
            },
        )


def enforce(
    ip: str,
    *,
    minute: MinuteLimiter,
    usage: DailyUsage | None,
    settings: Settings,
    now: datetime,
    endpoint: str = ASK,
) -> None:
    """Apply the per-minute limit, then the per-day one, of `endpoint` (ASK or SEARCH) to
    the client's limit_subject; raise RateLimited when over. Each endpoint counts apart.

    `usage` is None only when there is no database connection (tests that stub it out):
    the per-day limit is then skipped.
    """
    per_minute, per_day = settings.rate_limit_per_minute, settings.rate_limit_per_day
    if endpoint == SEARCH:
        per_minute = settings.search_rate_limit_per_minute
        per_day = settings.search_rate_limit_per_day
    subject = limit_subject(ip, settings.rate_limit_ipv6_prefix)
    key = subject if endpoint == ASK else f"{endpoint}|{subject}"
    wait = minute.hit(key, per_minute)
    if wait is not None:
        raise RateLimited("minute", per_minute, max(math.ceil(wait), 1), endpoint)
    day = now.astimezone(UTC).date()
    if usage is not None and not usage.count_request(key, day, per_day):
        raise RateLimited("day", per_day, seconds_until_midnight(now), endpoint)


def answer_token_estimate(settings: Settings) -> int:
    """Upper estimate of the tokens one answer spends: the context budget, the prompt
    overhead (system prompt and question, settings.eval_prompt_overhead_tokens) and the
    output budget. Reserved before the LLM call; the real count replaces it after."""
    return (
        settings.context_max_tokens
        + settings.eval_prompt_overhead_tokens
        + (settings.output_max_tokens)
    )


def reserve_budget(usage: DailyUsage | None, settings: Settings, day: date) -> int | None:
    """Reserve answer_token_estimate() public tokens of `day`. Returns the reserved amount
    (0 when there is no budget to keep: switched off or no database), or None when the
    reservation does not fit and the request must go search-only."""
    if usage is None or settings.daily_token_budget <= 0:
        return 0
    estimate = answer_token_estimate(settings)
    if usage.reserve_tokens(day, PUBLIC, estimate, settings.daily_token_budget):
        return estimate
    return None


# --- readiness -----------------------------------------------------------------------------


def missing_tables(conn: psycopg.Connection) -> list[str]:
    """Problems for /readyz when migration 002 (limits and cache) was not applied to the
    schema where migrations create tables (the first of the search path)."""
    sql = "SELECT to_regclass(format('%%I.%%I', current_schema(), %s::text))"
    missing = [t for t in TABLES if conn.execute(sql, (t,)).fetchone()[0] is None]
    if not missing:
        return []
    return [f"faltan las tablas {', '.join(missing)}: correr `make migrate`"]


def config_warnings(settings: Settings) -> list[str]:
    """Settings that work but are probably wrong in production (for /readyz and startup)."""
    if settings.env != "prod":
        return []
    warnings = []
    spec = settings.forwarded_allow_ips.replace(" ", "")
    if spec == DEFAULT_FORWARDED_ALLOW_IPS.replace(" ", ""):
        warnings.append(
            "FORWARDED_ALLOW_IPS tiene el valor por defecto: detrás del proxy del hosting "
            "todas las consultas compartirían un mismo límite por IP"
        )
    elif spec == "*":
        warnings.append(
            "FORWARDED_ALLOW_IPS='*': la IP se toma del primer valor de X-Forwarded-For, "
            "que el cliente puede falsificar para saltear el límite por IP"
        )
    if not settings.eval_key:
        warnings.append("EVAL_KEY vacío: las corridas de evals cuentan contra el límite por IP")
    if not settings.client_key_secret:
        warnings.append(
            "CLIENT_KEY_SECRET vacío: la clave de los clientes cambia en cada reinicio y el "
            "límite diario por IP vuelve a cero"
        )
    if all("localhost" in o or "127.0.0.1" in o for o in settings.cors_origins):
        warnings.append("CORS_ORIGINS solo permite orígenes locales: el front no podrá llamar")
    if settings.daily_token_budget <= 0:
        warnings.append("DAILY_TOKEN_BUDGET desactivado: no hay tope diario de tokens")
    return warnings
