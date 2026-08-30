"""Request/response contracts (validation + clear API shapes)."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---- sessions --------------------------------------------------------------
class SessionCreate(BaseModel):
    title: str = Field(default="New chat", max_length=200)
    user_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionOut(BaseModel):
    id: UUID
    title: str
    user_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# ---- messages / citations / artifacts -------------------------------------
class Citation(BaseModel):
    episode_title: str
    guest: str | None = None
    source_url: str | None = None
    publish_date: date | None = None
    chunk_id: UUID | None = None
    score: float | None = None


class Artifact(BaseModel):
    type: Literal["markdown", "html"]
    title: str = "Artifact"
    content: str


class MessageOut(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    content: str
    provider: str | None = None
    model: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    artifact: Artifact | None = None
    created_at: datetime


# ---- chat ------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: UUID
    message: str = Field(min_length=1, max_length=8000)


# ---- access requests -------------------------------------------------------
class AccessRequestCreate(BaseModel):
    name: str = Field(default="", max_length=120)
    email: str = Field(min_length=3, max_length=254)
    reason: str = Field(default="", max_length=1000)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


class AccessRequestOut(BaseModel):
    id: UUID
    name: str
    email: str
    reason: str
    status: str
    created_at: datetime
    updated_at: datetime


# ---- health ----------------------------------------------------------------
class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    provider: str
    model: str
    agent_backend: str
    checks: dict[str, Any]
