-- Etapa 2: index versions, chunks and the embedding cache.
-- Same SQL locally and on Supabase (rule 6). Applied by `python -m app.db migrate`.
--
-- Search is exact (no ANN index): with ~10,000 chunks a sequential cosine scan is fast and
-- never misses a neighbour (PLAN, "Stack").
--
-- Vector dimension: the `embedding` columns use the untyped `vector` type and the dimension
-- is fixed per index version (index_versions.embedding_dim) when the version is created.
-- A trigger rejects any chunk whose vector does not have its version's dimension, and the
-- backend refuses to start when the active version's model or dimension differs from its
-- settings (rule 4). This lets an experiment with another model (Etapa 6) live next to the
-- current index without a schema change. An approximate index would need a fixed dimension;
-- if one is ever added, use an expression index on `embedding::vector(<dim>)`.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS index_versions (
    index_version       text PRIMARY KEY,  -- hash of corpus_version + chunking + embedding model
    embedding_model     text NOT NULL,     -- canonical name, e.g. BAAI/bge-m3
    embedding_provider  text NOT NULL,     -- ollama | openai_compat | fake
    embedding_dim       integer NOT NULL CHECK (embedding_dim > 0),
    embedding_normalize boolean NOT NULL,
    corpus_version      text NOT NULL,
    chunking_config     jsonb NOT NULL,
    chunk_count         integer NOT NULL CHECK (chunk_count >= 0),
    active              boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    activated_at        timestamptz
);

-- At most one active version. app/index.py switches it inside one transaction, so readers
-- always see exactly one once the first index has been built.
CREATE UNIQUE INDEX IF NOT EXISTS index_versions_one_active
    ON index_versions (active) WHERE active;

CREATE TABLE IF NOT EXISTS chunks (
    index_version    text NOT NULL REFERENCES index_versions (index_version) ON DELETE CASCADE,
    chunk_id         text NOT NULL,     -- "<unit_id>#<n>", stable for a given chunking config
    unit_id          text NOT NULL,
    work_id          text NOT NULL,
    edition_id       text NOT NULL,
    lang             text NOT NULL,
    divisions        jsonb NOT NULL,    -- [["book","2"],["chapter","6"]]
    seq              integer NOT NULL,  -- order of the chunk inside its edition
    bekker_start     integer,           -- encoded as in app/bekker.py
    bekker_end       integer,
    bekker_precision text NOT NULL,
    text             text NOT NULL,     -- shown to people and quoted
    embed_text       text NOT NULL,     -- header + text: what was embedded
    embed_sha256     text NOT NULL,     -- key into embedding_cache
    -- Hook for Etapa 6 (hybrid search): the text-search configuration of the chunk's
    -- language ('english', 'spanish', 'simple'). A later migration can add
    --   tsv tsvector GENERATED ALWAYS AS (to_tsvector(ts_config, text)) STORED
    -- plus a GIN index, without touching existing rows.
    ts_config        regconfig NOT NULL DEFAULT 'simple',
    embedding        vector NOT NULL,
    PRIMARY KEY (index_version, chunk_id),
    CHECK (bekker_end IS NULL OR bekker_start <= bekker_end)
);

CREATE INDEX IF NOT EXISTS chunks_bekker
    ON chunks (index_version, work_id, bekker_start, bekker_end);
CREATE INDEX IF NOT EXISTS chunks_order
    ON chunks (index_version, edition_id, seq);

CREATE OR REPLACE FUNCTION chunks_check_dim() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    expected integer;
BEGIN
    SELECT embedding_dim INTO expected
    FROM index_versions WHERE index_version = NEW.index_version;
    IF vector_dims(NEW.embedding) <> expected THEN
        RAISE EXCEPTION 'chunk % has dimension %, index version % expects %',
            NEW.chunk_id, vector_dims(NEW.embedding), NEW.index_version, expected;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS chunks_check_dim ON chunks;
CREATE TRIGGER chunks_check_dim
    BEFORE INSERT OR UPDATE OF embedding ON chunks
    FOR EACH ROW EXECUTE FUNCTION chunks_check_dim();

-- Embeddings cached by canonical model and sha256 of the embedded text, so a chunking
-- change only embeds what is new (PLAN, "Contratos de datos"). Provider and normalisation
-- are stored and checked on read: a vector from another provider is treated as a miss and
-- replaced, so an index never mixes providers.
CREATE TABLE IF NOT EXISTS embedding_cache (
    model       text NOT NULL,
    text_sha256 text NOT NULL,
    provider    text NOT NULL,
    normalized  boolean NOT NULL,
    dim         integer NOT NULL,
    embedding   vector NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model, text_sha256)
);
