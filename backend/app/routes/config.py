"""Runtime model configuration: list selectable models and switch provider/model
live (no restart). Powers the model dropdown in the UI."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import runtime
from ..config import get_settings

router = APIRouter(prefix="/config", tags=["config"])


class ModelSelection(BaseModel):
    provider: str  # "cloud" | "local"
    model: str | None = None


@router.get("/models")
async def list_models() -> dict:
    return {"options": await runtime.available_options(), "current": runtime.current_selection_id()}


@router.post("/model")
async def set_model(body: ModelSelection) -> dict:
    # In a deployed environment the provider/model are fixed by config; don't let
    # visitors switch to a costlier model (or a broken provider) and burn quota.
    if get_settings().app_env != "local":
        raise HTTPException(status_code=403, detail="Model switching is disabled in this deployment.")
    try:
        runtime.set_selection(body.provider, body.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "current": runtime.current_selection_id(),
        "provider": runtime.get_provider(),
        "model": runtime.active_model_label(),
    }
