"""Health endpoint. Surfaces each dependency (db, ollama, litellm, knowledge base)
so failures are diagnosable at a glance."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter

from ..config import get_settings
from ..llm import gateway
from ..repository import count_chunks, ping
from ..schemas import HealthOut

log = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


async def _check_db() -> dict:
    try:
        await ping()
        return {"ok": True, "chunks": await count_chunks()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def _check_url(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(url)
        return {"ok": r.status_code < 500, "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    s = get_settings()
    db = await _check_db()
    checks: dict = {"db": db}

    # Only probe services this deployment actually uses, so a pure-cloud deploy
    # (OpenAI chat + OpenAI embeddings) doesn't report phantom failures or eat the
    # health latency on unreachable URLs.
    if s.embed_provider.lower() == "ollama" or not s.is_cloud:
        checks["ollama"] = await _check_url(f"{s.ollama_url}/api/tags")
    if s.agent_backend == "agent_sdk":
        checks["litellm"] = await _check_url(f"{s.litellm_proxy_url}/health/liveliness")

    provider, model = gateway.active_labels()  # reflects live selection + fallback
    return HealthOut(
        status="ok" if db.get("ok") else "degraded",
        provider=provider,
        model=model,
        agent_backend=s.agent_backend,
        checks=checks,
    )
