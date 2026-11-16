import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import limits
from app.db import MIGRATIONS_DIR, migrate
from app.limits import DailyUsage, MinuteLimiter, RateLimited, client_ip
from app.settings import Settings
from tests.test_db_support import db_url, temp_schema_connection

DAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 23, 59, 30, tzinfo=UTC)


def lim_settings(**overrides) -> Settings:
    values = {
        "env": "local",
        "rate_limit_per_minute": 5,
        "rate_limit_per_day": 30,
        "daily_token_budget": 120_000,
        "eval_key": "",
    }
    values.update(overrides)
    return Settings(**values)


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# --- client address ----------------------------------------------------------------------

PROXY = "10.0.0.5"
TRUSTED = "10.0.0.0/8"


@pytest.mark.parametrize(
    "peer, xff, trusted, expected",
    [
        ("203.0.113.7", None, TRUSTED, "203.0.113.7"),  # no header: the peer
        ("203.0.113.7", "1.2.3.4", TRUSTED, "203.0.113.7"),  # untrusted peer: header ignored
        (PROXY, "198.51.100.9", TRUSTED, "198.51.100.9"),  # through the proxy
        (PROXY, "6.6.6.6, 198.51.100.9", TRUSTED, "198.51.100.9"),  # forged left part ignored
        (PROXY, "198.51.100.9, 10.0.0.7", TRUSTED, "198.51.100.9"),  # two trusted hops
        (PROXY, "10.0.0.8, 10.0.0.7", TRUSTED, "10.0.0.8"),  # all trusted: the leftmost
        (PROXY, "198.51.100.9:4242", TRUSTED, "198.51.100.9"),  # port removed
        (PROXY, "[2001:db8::1]:443", TRUSTED, "2001:db8::1"),
        (PROXY, "2001:db8::1", TRUSTED, "2001:db8::1"),
        (PROXY, "6.6.6.6, 198.51.100.9", "*", "6.6.6.6"),  # "*": leftmost, forgeable
        (PROXY, " , ", TRUSTED, PROXY),  # empty header: the peer
        ("testclient", "198.51.100.9", "testclient", "198.51.100.9"),  # literal
        ("127.0.0.1", "198.51.100.9", "127.0.0.1,::1", "198.51.100.9"),  # uvicorn default
        ("::1", "198.51.100.9", "127.0.0.1,::1", "198.51.100.9"),
        ("::ffff:10.0.0.5", "198.51.100.9", TRUSTED, "198.51.100.9"),  # IPv4-mapped peer
        (None, "198.51.100.9", TRUSTED, "unknown"),
    ],
)
def test_client_ip(peer, xff, trusted, expected):
    assert client_ip(peer, xff, trusted) == expected


def _uvicorn_client(peer: str, xff: str, trusted: str) -> str:
    """The client address uvicorn's --proxy-headers middleware gives the app."""
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"][0]

    scope = {
        "type": "http",
        "client": (peer, 1234),
        "headers": [(b"x-forwarded-for", xff.encode())],
    }
    asyncio.run(ProxyHeadersMiddleware(app, trusted_hosts=trusted)(scope, None, None))
    return seen["client"]


@pytest.mark.parametrize(
    "peer, xff, trusted",
    [
        (PROXY, "198.51.100.9", TRUSTED),
        (PROXY, "6.6.6.6, 198.51.100.9, 10.0.0.7", TRUSTED),
        (PROXY, "10.0.0.8, 10.0.0.7", TRUSTED),
        (PROXY, "6.6.6.6, 198.51.100.9", "*"),
        ("203.0.113.7", "1.2.3.4", TRUSTED),
        ("127.0.0.1", "198.51.100.9", "127.0.0.1,::1"),
    ],
)
def test_client_ip_agrees_with_uvicorn_proxy_headers(peer, xff, trusted):
    assert client_ip(peer, xff, trusted) == _uvicorn_client(peer, xff, trusted)


@pytest.mark.parametrize(
    "peer, xff, trusted",
    [(PROXY, "6.6.6.6, 198.51.100.9", TRUSTED), (PROXY, "198.51.100.9", "*")],
)
def test_client_ip_is_stable_after_uvicorn_rewrote_the_peer(peer, xff, trusted):
    """With --proxy-headers, the app sees uvicorn's client as the peer: same answer."""
    rewritten = _uvicorn_client(peer, xff, trusted)
    assert client_ip(rewritten, xff, trusted) == client_ip(peer, xff, trusted)


def test_client_key_hashes_day_and_address():
    key = limits.client_key("198.51.100.9", DAY, "s3cret")
    assert len(key) == 64 and "198.51.100.9" not in key
    assert key == limits.client_key("198.51.100.9", DAY, "s3cret")
    assert key != limits.client_key("198.51.100.9", DAY + timedelta(days=1), "s3cret")
    assert key != limits.client_key("198.51.100.10", DAY, "s3cret")


def test_client_key_cannot_be_reversed_without_the_secret():
    # A plain sha256("<day>|<ip>") maps back to the address by trying the 2^32 IPv4s.
    import hashlib

    ip = "203.0.113.77"
    plain = hashlib.sha256(f"{DAY.isoformat()}|{ip}".encode()).hexdigest()
    assert limits.client_key(ip, DAY, "s3cret") != plain
    assert limits.client_key(ip, DAY, "s3cret") != limits.client_key(ip, DAY, "other")
    # Without a configured secret, a random one per process (still not the plain hash).
    assert limits.client_key(ip, DAY, "") == limits.client_key(ip, DAY, "") != plain


@pytest.mark.parametrize(
    "ip, expected",
    [
        ("203.0.113.77", "203.0.113.77"),
        ("2001:db8:1:2::3b", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:1:2:3", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.77", "203.0.113.77"),  # IPv4-mapped
        ("unknown", "unknown"),
    ],
)
def test_ipv6_clients_are_limited_by_network(ip, expected):
    assert limits.limit_subject(ip, 64) == expected


def test_one_ipv6_network_shares_the_minute_limit():
    s = lim_settings(rate_limit_per_minute=5, rate_limit_per_day=0)
    minute = limits.MinuteLimiter(Clock())
    for i in range(5):
        limits.enforce(f"2001:db8:1:2::{i:x}", minute=minute, usage=None, settings=s, now=NOW)
    with pytest.raises(limits.RateLimited):
        limits.enforce("2001:db8:1:2::ff", minute=minute, usage=None, settings=s, now=NOW)
    limits.enforce("2001:db8:1:3::1", minute=minute, usage=None, settings=s, now=NOW)  # other /64


def test_eval_key():
    assert not limits.is_eval_request("anything", lim_settings(eval_key=""))
    assert not limits.is_eval_request("", lim_settings(eval_key=""))
    assert not limits.is_eval_request(None, lim_settings(eval_key="s3cret"))
    assert not limits.is_eval_request("wrong", lim_settings(eval_key="s3cret"))
    assert limits.is_eval_request("s3cret", lim_settings(eval_key="s3cret"))


# --- per-minute limiter --------------------------------------------------------------------


def test_sixth_request_in_a_minute_is_refused():
    clock = Clock()
    limiter = MinuteLimiter(clock)
    for _ in range(5):
        assert limiter.hit("a", 5) is None
        clock.t += 1
    wait = limiter.hit("a", 5)
    assert wait == pytest.approx(55.0)  # the first request leaves the window at t0 + 60
    assert limiter.hit("b", 5) is None  # other clients are not affected


def test_window_slides_and_refusals_are_not_counted():
    clock = Clock()
    limiter = MinuteLimiter(clock)
    for _ in range(2):
        assert limiter.hit("a", 2) is None
    for _ in range(10):  # retrying does not extend the wait
        assert limiter.hit("a", 2) == pytest.approx(60.0)
    clock.t += 60
    assert limiter.hit("a", 2) is None


def test_limit_lowered_at_runtime_waits_for_enough_room():
    clock = Clock()
    limiter = MinuteLimiter(clock)
    for _ in range(4):
        assert limiter.hit("a", 10) is None
        clock.t += 10
    # 4 hits at t0, +10, +20, +30; now +40 with a limit of 2: room after the 3rd leaves.
    assert limiter.hit("a", 2) == pytest.approx(40.0)


def test_zero_disables_the_minute_limit():
    limiter = MinuteLimiter(Clock())
    assert all(limiter.hit("a", 0) is None for _ in range(100))
    assert len(limiter) == 0


def test_idle_clients_are_forgotten():
    clock = Clock()
    limiter = MinuteLimiter(clock)
    for i in range(50):
        limiter.hit(f"ip{i}", 5)
    assert len(limiter) == 50
    clock.t += 61
    limiter.hit("new", 5)
    assert len(limiter) == 1


# --- enforce, messages, budget -------------------------------------------------------------


class MemoryUsage:
    """In-memory stand-in for DailyUsage (same methods)."""

    def __init__(self):
        self.requests: dict[tuple[str, date], int] = {}
        self.tokens: dict[tuple[date, str], int] = {}

    def count_request(self, ip, day, limit):
        if limit <= 0:
            return True
        n = self.requests.get((ip, day), 0)
        if n >= limit:
            return False
        self.requests[(ip, day)] = n + 1
        return True

    def tokens_used(self, day, source=limits.PUBLIC):
        return self.tokens.get((day, source), 0)

    def add_tokens(self, day, source, tokens):
        self.tokens[(day, source)] = self.tokens.get((day, source), 0) + tokens

    def reserve_tokens(self, day, source, tokens, budget):
        if self.tokens_used(day, source) + tokens > budget:
            return False
        self.add_tokens(day, source, tokens)
        return True

    def settle_tokens(self, day, source, reserved, used):
        self.tokens[(day, source)] = max(self.tokens_used(day, source) + used - reserved, 0)


def test_enforce_minute_then_day():
    s = lim_settings(rate_limit_per_minute=3, rate_limit_per_day=4)
    clock, usage = Clock(), MemoryUsage()
    minute = MinuteLimiter(clock)
    for _ in range(3):
        limits.enforce("ip", minute=minute, usage=usage, settings=s, now=NOW)
    with pytest.raises(RateLimited) as exc:
        limits.enforce("ip", minute=minute, usage=usage, settings=s, now=NOW)
    assert exc.value.scope == "minute" and exc.value.retry_after == 60
    clock.t += 60
    limits.enforce("ip", minute=minute, usage=usage, settings=s, now=NOW)  # 4th of the day
    clock.t += 60
    with pytest.raises(RateLimited) as exc:
        limits.enforce("ip", minute=minute, usage=usage, settings=s, now=NOW)
    assert exc.value.scope == "day"
    assert exc.value.retry_after == 30  # NOW is 23:59:30 UTC
    # Without a database the per-day limit is skipped.
    clock.t += 60
    limits.enforce("ip", minute=minute, usage=None, settings=s, now=NOW)


def test_rate_limited_messages_are_spanish_and_name_the_limit():
    minute = RateLimited("minute", 5, 42).message
    assert "5 preguntas por minuto" in minute and "42 segundos" in minute
    day = RateLimited("day", 30, 3600).message
    assert "30 preguntas por día" in day and "00:00 UTC" in day


def test_seconds_until_midnight():
    assert limits.seconds_until_midnight(NOW) == 30
    assert limits.seconds_until_midnight(datetime(2026, 1, 1, tzinfo=UTC)) == 86_400


def test_budget_is_reserved_before_the_llm_call():
    usage = MemoryUsage()
    s = lim_settings(daily_token_budget=7000)
    estimate = limits.answer_token_estimate(s)
    assert estimate == s.context_max_tokens + s.eval_prompt_overhead_tokens + s.output_max_tokens
    usage.add_tokens(DAY, limits.EVAL, 50_000)  # eval runs do not count
    assert limits.reserve_budget(usage, s, DAY) == estimate
    assert limits.reserve_budget(usage, s, DAY) == estimate
    assert limits.reserve_budget(usage, s, DAY) is None  # a third would pass 7000
    assert usage.tokens_used(DAY) == 2 * estimate
    usage.settle_tokens(DAY, limits.PUBLIC, estimate, 400)  # the real count replaces it
    assert usage.tokens_used(DAY) == estimate + 400
    assert limits.reserve_budget(usage, s, DAY + timedelta(days=1)) == estimate
    assert limits.reserve_budget(usage, lim_settings(daily_token_budget=0), DAY) == 0
    assert limits.reserve_budget(None, s, DAY) == 0


def test_config_warnings_only_in_production():
    assert limits.config_warnings(lim_settings(env="local")) == []
    prod = lim_settings(env="prod")
    warnings = " ".join(limits.config_warnings(prod))
    for name in ("FORWARDED_ALLOW_IPS", "EVAL_KEY", "CORS_ORIGINS", "CLIENT_KEY_SECRET"):
        assert name in warnings
    star = " ".join(limits.config_warnings(lim_settings(env="prod", forwarded_allow_ips="*")))
    assert "falsificar" in star
    good = lim_settings(
        env="prod",
        forwarded_allow_ips="10.0.0.0/8",
        eval_key="k",
        client_key_secret="c",
        cors_origins=["https://a-rag.example.org"],
    )
    assert limits.config_warnings(good) == []


# --- against Postgres ----------------------------------------------------------------------


@pytest.mark.db
def test_daily_counter_in_postgres():
    with temp_schema_connection() as conn:
        usage = DailyUsage(conn, retention_days=7)
        assert [usage.count_request("198.51.100.9", DAY, 3) for _ in range(4)] == [
            True,
            True,
            True,
            False,
        ]
        assert usage.count_request("198.51.100.10", DAY, 3)  # another client
        assert usage.count_request("198.51.100.9", DAY + timedelta(days=1), 3)  # next day
        assert usage.count_request("198.51.100.9", DAY, 0)  # 0 disables
        rows = conn.execute(
            "SELECT day, client_key, requests FROM rate_limit_daily ORDER BY day, requests"
        ).fetchall()
        assert sorted(r[2] for r in rows) == [1, 1, 3]
        assert all("198.51.100" not in r[1] for r in rows)  # the address is not stored
        assert (DAY, limits.client_key("198.51.100.9", DAY, ""), 3) in rows


@pytest.mark.db
def test_old_counters_are_purged():
    with temp_schema_connection() as conn:
        usage = DailyUsage(conn, retention_days=7)
        old = DAY - timedelta(days=8)
        usage.count_request("198.51.100.9", old, 3)
        usage.count_request("198.51.100.9", DAY - timedelta(days=7), 3)
        usage.count_request("198.51.100.9", DAY, 3)  # first today: purges
        days = [r[0] for r in conn.execute("SELECT day FROM rate_limit_daily ORDER BY day")]
        assert days == [DAY - timedelta(days=7), DAY]


@pytest.mark.db
def test_token_usage_in_postgres():
    with temp_schema_connection() as conn:
        usage = DailyUsage(conn)
        assert usage.tokens_used(DAY) == 0
        usage.add_tokens(DAY, limits.PUBLIC, 1200)
        usage.add_tokens(DAY, limits.PUBLIC, 800)
        usage.add_tokens(DAY, limits.EVAL, 3000)
        assert usage.tokens_used(DAY) == 2000
        assert usage.tokens_used(DAY, limits.EVAL) == 3000
        assert usage.tokens_used(DAY + timedelta(days=1)) == 0
        row = conn.execute(
            "SELECT requests FROM token_usage_daily WHERE day = %s AND source = 'public'", (DAY,)
        ).fetchone()
        assert row[0] == 2
        with pytest.raises(psycopg.errors.CheckViolation):
            usage.add_tokens(DAY, "other", 1)


@pytest.mark.db
def test_concurrent_reservations_cannot_overshoot_the_budget():
    # The old check-then-act let every concurrent request through: here 20 requests race
    # for a budget that holds 3 reservations.
    from concurrent.futures import ThreadPoolExecutor

    with temp_schema_connection() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        conn.commit()  # make the migrated tables visible to the other connections

        def reserve(_):
            with psycopg.connect(
                db_url(), autocommit=True, options=f"-c search_path={schema},public"
            ) as c:
                return DailyUsage(c).reserve_tokens(DAY, limits.PUBLIC, 1000, 3000)

        with ThreadPoolExecutor(max_workers=10) as pool:
            granted = list(pool.map(reserve, range(20)))
        assert sum(granted) == 3
        usage = DailyUsage(conn)
        assert usage.tokens_used(DAY) == 3000
        usage.settle_tokens(DAY, limits.PUBLIC, 1000, 250)
        assert usage.tokens_used(DAY) == 2250
        assert not usage.reserve_tokens(DAY, limits.PUBLIC, 5000, 3000)  # never fits
        assert usage.reserve_tokens(DAY + timedelta(days=1), limits.PUBLIC, 3000, 3000)


@pytest.mark.db
def test_migrations_create_the_tables_and_are_idempotent():
    with temp_schema_connection() as conn:
        assert limits.missing_tables(conn) == []
        assert migrate(conn) == []  # applying again does nothing


@pytest.mark.db
def test_missing_tables_are_reported(tmp_path):
    (tmp_path / "001_init.sql").write_text((MIGRATIONS_DIR / "001_init.sql").read_text())
    with temp_schema_connection() as conn:
        schema = f"test_{uuid.uuid4().hex[:12]}"
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET LOCAL search_path TO {schema}, public")
        migrate(conn, tmp_path)  # only 001
        problems = limits.missing_tables(conn)
        assert len(problems) == 1 and "make migrate" in problems[0]
        assert "answer_cache" in problems[0]


@pytest.mark.db
def test_row_level_security_hides_tables_from_other_roles():
    """What Supabase's `anon` role would see through the Data API: nothing."""
    tables = [
        "schema_migrations",
        "index_versions",
        "chunks",
        "embedding_cache",
        *limits.TABLES,
    ]
    with temp_schema_connection() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        flags = dict(
            conn.execute(
                """
                SELECT c.relname, c.relrowsecurity FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relkind = 'r'
                """,
                (schema,),
            ).fetchall()
        )
        assert all(flags[t] for t in tables), flags
        DailyUsage(conn).add_tokens(DAY, limits.PUBLIC, 10)
        # A role with table grants but no policy (rolled back with the test transaction).
        role = f"anon_{uuid.uuid4().hex[:8]}"
        conn.execute(f"CREATE ROLE {role} NOLOGIN")
        conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}")
        conn.execute(f"SET LOCAL ROLE {role}")
        assert conn.execute("SELECT count(*) FROM token_usage_daily").fetchone()[0] == 0
        conn.execute("RESET ROLE")
        assert conn.execute("SELECT count(*) FROM token_usage_daily").fetchone()[0] == 1
