-- Etapa 7: per-IP daily request counters, the global daily token budget and the exact
-- answer cache for POST /ask (app/limits.py, app/cache.py).
-- Same SQL locally and on Supabase (rule 6). Applied by `python -m app.db migrate`.
--
-- Client addresses are never stored: rate_limit_daily keeps sha256("<day>|<ip>"), so the
-- rows of different days cannot be linked to each other. The backend deletes rows older
-- than settings.rate_limit_retention_days as it writes new ones.

CREATE TABLE IF NOT EXISTS rate_limit_daily (
    day        date NOT NULL,  -- UTC
    client_key text NOT NULL,  -- sha256 of "<day>|<client ip>", never the address itself
    requests   integer NOT NULL DEFAULT 0 CHECK (requests >= 0),
    PRIMARY KEY (day, client_key)
);

-- LLM tokens (prompt + completion, as the provider reports them) per UTC day. "public" is
-- what the daily budget counts; "eval" are runs identified with X-Eval-Key, kept apart
-- because the budget is set below the provider's quota to leave room for them.
CREATE TABLE IF NOT EXISTS token_usage_daily (
    day      date NOT NULL,
    source   text NOT NULL CHECK (source IN ('public', 'eval')),
    tokens   bigint NOT NULL DEFAULT 0 CHECK (tokens >= 0),
    requests integer NOT NULL DEFAULT 0 CHECK (requests >= 0),  -- answers that used the LLM
    PRIMARY KEY (day, source)
);

-- Exact answer cache. cache_key is a sha256 over every column below plus the settings that
-- change an answer; the question itself is stored only as a hash.
CREATE TABLE IF NOT EXISTS answer_cache (
    cache_key       text PRIMARY KEY,
    question_sha256 text NOT NULL,   -- of the normalised question
    answer_lang     text NOT NULL,
    index_version   text NOT NULL,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    filters         jsonb NOT NULL,  -- {"works": [...], "lang": ..., "include_dubious": ...}
    response        jsonb NOT NULL,  -- the public POST /ask response
    created_at      timestamptz NOT NULL DEFAULT now(),
    hits            integer NOT NULL DEFAULT 0,
    last_hit_at     timestamptz
);

CREATE INDEX IF NOT EXISTS answer_cache_created ON answer_cache (created_at);
