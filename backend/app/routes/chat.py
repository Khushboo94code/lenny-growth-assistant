"""Chat endpoint: streams a grounded answer as Server-Sent Events."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import repository as repo
from ..agent.orchestrator import sse_stream
from ..schemas import ChatRequest

router = APIRouter(tags=["chat"])

# Lightweight in-process rate limit on the (expensive) chat endpoint. Keyed on the
# real client IP forwarded by Cloudflare (CF-Connecting-IP), falling back to the
# socket peer. uvicorn runs a single worker here, so a module-level dict suffices.
# This is defense-in-depth; the hard spend ceiling is an OpenAI usage/budget cap.
_RATE_MAX = 15          # requests allowed ...
_RATE_WINDOW = 60.0     # ... per this many seconds, per client
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(request: Request) -> None:
    key = request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else "unknown"
    )
    now = time.monotonic()
    dq = _hits[key]
    while dq and now - dq[0] > _RATE_WINDOW:
        dq.popleft()
    if len(dq) >= _RATE_MAX:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded — please wait a moment and try again.",
        )
    dq.append(now)
    if len(_hits) > 1024:  # opportunistic cleanup of idle keys
        for k in [k for k, v in _hits.items() if not v]:
            del _hits[k]


@router.post("/chat")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    _rate_limit(request)
    if await repo.get_session(body.session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return StreamingResponse(
        sse_stream(body.session_id, body.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering for token streaming
        },
    )
