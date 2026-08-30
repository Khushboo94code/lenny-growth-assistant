"""Data-access layer. Thin async functions over the psycopg pool; rows come back
as dicts. Vectors go in/out as `::vector` literals."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Json

from .db import get_pool


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


# ---- sessions --------------------------------------------------------------
async def create_session(title: str, user_metadata: dict[str, Any] | None = None) -> dict:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO sessions (title, user_metadata) VALUES (%s, %s) "
                "RETURNING id, title, user_metadata, created_at, updated_at",
                (title, Json(user_metadata or {})),
            )
            return await cur.fetchone()


async def list_sessions() -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, title, user_metadata, created_at, updated_at "
                "FROM sessions ORDER BY updated_at DESC"
            )
            return await cur.fetchall()


async def get_session(session_id: UUID) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, title, user_metadata, created_at, updated_at "
                "FROM sessions WHERE id = %s",
                (session_id,),
            )
            return await cur.fetchone()


async def touch_session(session_id: UUID, title: str | None = None) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            if title is not None:
                await cur.execute(
                    "UPDATE sessions SET updated_at = now(), title = %s WHERE id = %s",
                    (title, session_id),
                )
            else:
                await cur.execute(
                    "UPDATE sessions SET updated_at = now() WHERE id = %s", (session_id,)
                )
        await conn.commit()


# ---- messages --------------------------------------------------------------
async def add_message(
    session_id: UUID,
    role: str,
    content: str,
    provider: str | None = None,
    model: str | None = None,
    citations: list[dict] | None = None,
    artifact: dict | None = None,
) -> dict:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO messages (session_id, role, content, provider, model, citations, artifact) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, session_id, role, content, provider, model, citations, artifact, created_at",
                (
                    session_id,
                    role,
                    content,
                    provider,
                    model,
                    Json(citations or []),
                    Json(artifact) if artifact is not None else None,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row


async def list_messages(session_id: UUID) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, session_id, role, content, provider, model, citations, artifact, created_at "
                "FROM messages WHERE session_id = %s ORDER BY created_at",
                (session_id,),
            )
            return await cur.fetchall()


async def get_history(session_id: UUID, limit: int = 20) -> list[dict]:
    """Recent turns as {role, content} for model context (oldest -> newest)."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id = %s AND role IN ('user','assistant') "
                "ORDER BY created_at DESC LIMIT %s",
                (session_id, limit),
            )
            rows = await cur.fetchall()
    return list(reversed(rows))


# ---- transcript chunks / retrieval ----------------------------------------
async def insert_chunks(rows: list[dict]) -> int:
    """Bulk insert chunk dicts with keys:
    episode_id, episode_title, guest, source_url, publish_date, chunk_index, chunk_text, embedding(list)."""
    if not rows:
        return 0
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for r in rows:
                await cur.execute(
                    "INSERT INTO transcript_chunks "
                    "(episode_id, episode_title, guest, source_url, publish_date, chunk_index, chunk_text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)",
                    (
                        r["episode_id"],
                        r["episode_title"],
                        r.get("guest"),
                        r.get("source_url"),
                        r.get("publish_date"),
                        r["chunk_index"],
                        r["chunk_text"],
                        vector_literal(r["embedding"]),
                    ),
                )
        await conn.commit()
    return len(rows)


async def similarity_search(embedding: list[float], top_k: int) -> list[dict]:
    """Cosine top-k. `score` = 1 - cosine_distance (higher = more similar)."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, episode_id, episode_title, guest, source_url, publish_date, "
                "chunk_index, chunk_text, 1 - (embedding <=> %s::vector) AS score "
                "FROM transcript_chunks ORDER BY embedding <=> %s::vector LIMIT %s",
                (vector_literal(embedding), vector_literal(embedding), top_k),
            )
            return await cur.fetchall()


async def count_chunks() -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM transcript_chunks")
            row = await cur.fetchone()
            return int(row[0]) if row else 0


# ---- access requests -------------------------------------------------------
async def create_access_request(name: str, email: str, reason: str) -> dict:
    """Upsert keyed on lower(email): re-submitting updates the details and timestamp
    but never downgrades a request that's already been approved/denied."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO access_requests (name, email, reason) VALUES (%s, %s, %s) "
                "ON CONFLICT (lower(email)) DO UPDATE "
                "SET name = EXCLUDED.name, reason = EXCLUDED.reason, updated_at = now() "
                "RETURNING id, name, email, reason, status, created_at, updated_at",
                (name, email, reason),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row


async def list_access_requests() -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, name, email, reason, status, created_at, updated_at "
                "FROM access_requests ORDER BY "
                "CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, created_at DESC"
            )
            return await cur.fetchall()


async def set_access_request_status(request_id: UUID, status: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "UPDATE access_requests SET status = %s, updated_at = now() WHERE id = %s "
                "RETURNING id, name, email, reason, status, created_at, updated_at",
                (status, request_id),
            )
            row = await cur.fetchone()
        await conn.commit()
    return row


async def ping() -> bool:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.fetchone()
    return True
