"""Embeddings. `EMBED_PROVIDER` selects the backend:
  - ollama -> nomic-embed-text (768 dims), fully local, no key
  - openai -> text-embedding-3-small (1536 dims), pure-cloud, no Ollama needed
The vector dimension is fixed per deployment (it defines the pgvector column), so
switching providers requires a matching EMBED_DIM and a fresh ingest."""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

_OPENAI_BATCH = 256  # inputs per OpenAI embeddings request


async def _embed_ollama_one(text: str, s: Settings) -> list[float]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{s.ollama_url}/api/embeddings",
            json={"model": s.embed_model, "prompt": text},
        )
        r.raise_for_status()
        emb = r.json().get("embedding")
    if not emb:
        raise RuntimeError(f"Empty embedding from Ollama for model {s.embed_model}")
    return emb


async def _embed_openai_batch(texts: list[str], s: Settings) -> list[list[float]]:
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when EMBED_PROVIDER=openai")
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), _OPENAI_BATCH):
            batch = texts[i : i + _OPENAI_BATCH]
            r = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {s.openai_api_key}"},
                json={"model": s.embed_model, "input": batch},
            )
            r.raise_for_status()
            data = r.json()["data"]
            data.sort(key=lambda d: d["index"])  # OpenAI returns index; keep input order
            out.extend(d["embedding"] for d in data)
    return out


async def embed_text(text: str) -> list[float]:
    s = get_settings()
    if s.embed_provider.lower() == "openai":
        return (await _embed_openai_batch([text], s))[0]
    return await _embed_ollama_one(text, s)


async def embed_texts(texts: list[str], concurrency: int = 4) -> list[list[float]]:
    """Embed many texts (order preserved). OpenAI batches in one request per chunk;
    Ollama fans out with bounded concurrency."""
    if not texts:
        return []
    s = get_settings()
    if s.embed_provider.lower() == "openai":
        return await _embed_openai_batch(texts, s)

    sem = asyncio.Semaphore(concurrency)

    async def _one(t: str) -> list[float]:
        async with sem:
            return await _embed_ollama_one(t, s)

    return await asyncio.gather(*[_one(t) for t in texts])
