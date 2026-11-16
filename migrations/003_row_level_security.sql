-- Etapa 7: no direct public access to the database (PLAN, Etapa 7).
--
-- Supabase publishes the `public` schema through its Data API to the `anon` and
-- `authenticated` roles. Row level security with no policies hides every row from those
-- roles. The backend connects as the owner of the tables (the role that ran the
-- migrations), which row level security does not restrict, so nothing changes for it or
-- for a local Postgres.
--
-- Every later migration that creates a table must enable row level security on it too.

ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE index_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE embedding_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_limit_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE token_usage_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE answer_cache ENABLE ROW LEVEL SECURITY;
