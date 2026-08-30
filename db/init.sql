-- Runs automatically on first Postgres container init.
-- The API also applies this idempotently at startup (app.db.init_db) so the
-- schema exists even when running natively without Docker.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sessions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title         TEXT NOT NULL DEFAULT 'New chat',
    user_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content     TEXT NOT NULL DEFAULT '',
    provider    TEXT,
    model       TEXT,
    citations   JSONB NOT NULL DEFAULT '[]'::jsonb,
    artifact    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

-- Knowledge base: chunked transcript text + embeddings + source metadata for citations.
-- Vector dim 768 matches nomic-embed-text. Changing the embed model => change this dim.
CREATE TABLE IF NOT EXISTS transcript_chunks (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id     TEXT NOT NULL,
    episode_title  TEXT NOT NULL,
    guest          TEXT,
    source_url     TEXT,
    publish_date   DATE,
    chunk_index    INT NOT NULL,
    chunk_text     TEXT NOT NULL,
    embedding      VECTOR(768) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_hnsw
    ON transcript_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_episode ON transcript_chunks(episode_id);

-- Access requests submitted from the public /welcome onboarding page. The owner
-- reviews these and grants access in Cloudflare Access (this is just the queue).
CREATE TABLE IF NOT EXISTS access_requests (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_access_requests_email
    ON access_requests (lower(email));
