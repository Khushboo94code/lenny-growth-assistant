"""Async Postgres access via a psycopg3 connection pool. Vectors are passed as
`::vector` literal casts, so no adapter registration / extension-timing dance."""
from __future__ import annotations

import logging

import psycopg
from psycopg_pool import AsyncConnectionPool

from .config import get_settings

log = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


def _schema_sql(dim: int) -> str:
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS sessions (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title         TEXT NOT NULL DEFAULT 'New chat',
        user_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS messages (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        role        TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
        content     TEXT NOT NULL DEFAULT '',
        provider    TEXT,
        model       TEXT,
        citations   JSONB NOT NULL DEFAULT '[]'::jsonb,
        artifact    JSONB,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

    CREATE TABLE IF NOT EXISTS transcript_chunks (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        episode_id     TEXT NOT NULL,
        episode_title  TEXT NOT NULL,
        guest          TEXT,
        source_url     TEXT,
        publish_date   DATE,
        chunk_index    INT NOT NULL,
        chunk_text     TEXT NOT NULL,
        embedding      VECTOR({dim}) NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_chunks_hnsw
        ON transcript_chunks USING hnsw (embedding vector_cosine_ops);
    CREATE INDEX IF NOT EXISTS idx_chunks_episode ON transcript_chunks(episode_id);

    -- Access requests: people who ask (from the public /welcome page) to be
    -- approved. Approval itself is granted in Cloudflare Access; this is the queue.
    CREATE TABLE IF NOT EXISTS access_requests (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name        TEXT NOT NULL DEFAULT '',
        email       TEXT NOT NULL,
        reason      TEXT NOT NULL DEFAULT '',
        status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied')),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_access_requests_email
        ON access_requests (lower(email));
    """


async def init_db() -> None:
    """Idempotently ensure the extension + schema (works with or without Docker init.sql)."""
    s = get_settings()
    async with await psycopg.AsyncConnection.connect(s.database_url) as conn:
        async with conn.cursor() as cur:
            await cur.execute(_schema_sql(s.embed_dim))
        await conn.commit()
    log.info("Database schema ensured")


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(conninfo=s.database_url, min_size=1, max_size=10, open=False)
        await _pool.open(wait=True, timeout=30)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
