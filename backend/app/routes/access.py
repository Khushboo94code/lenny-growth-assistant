"""Access-request intake for the public onboarding page, plus a small admin view.

The public /welcome page (a Cloudflare-Access "Bypass" path) posts to /request-access.
The owner reviews the queue via /admin/access-requests (reachable only to already
Access-approved users) and then approves people by adding their email to the
Cloudflare Access group — that is what actually grants entry."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from .. import repository as repo
from ..schemas import AccessRequestCreate, AccessRequestOut

log = logging.getLogger(__name__)
router = APIRouter(tags=["access"])

# Anti-spam on the public endpoint: cap submissions per client IP (real IP is
# forwarded by Cloudflare as CF-Connecting-IP).
_RATE_MAX = 5
_RATE_WINDOW = 3600.0  # seconds (1 hour)
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
        raise HTTPException(status_code=429, detail="Too many requests — please try again later.")
    dq.append(now)
    if len(_hits) > 4096:  # opportunistic cleanup of idle keys
        for k in [k for k, v in _hits.items() if not v]:
            del _hits[k]


@router.post("/request-access", response_model=AccessRequestOut)
async def request_access(body: AccessRequestCreate, request: Request) -> dict:
    """PUBLIC: submit a request to be approved. Idempotent per email."""
    _rate_limit(request)
    row = await repo.create_access_request(body.name.strip(), body.email, body.reason.strip())
    log.info("access_request email=%s status=%s", row["email"], row["status"])
    return row


@router.get("/admin/access-requests", response_model=list[AccessRequestOut])
async def list_access_requests() -> list[dict]:
    """Reachable only to Access-approved users (the whole host is gated except the
    bypassed /welcome and /request-access paths), so no extra auth is needed here."""
    return await repo.list_access_requests()


@router.post("/admin/access-requests/{request_id}", response_model=AccessRequestOut)
async def update_access_request(request_id: UUID, status: str) -> dict:
    """Mark a request approved/denied for your own tracking. The real grant still
    happens by adding the email to the Cloudflare Access group."""
    if status not in {"pending", "approved", "denied"}:
        raise HTTPException(status_code=400, detail="status must be pending, approved, or denied")
    row = await repo.set_access_request_status(request_id, status)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return row
