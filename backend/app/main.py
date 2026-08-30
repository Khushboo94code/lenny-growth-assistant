"""FastAPI application entrypoint: logging, request-id correlation, structured
error envelopes, dependency-aware startup, and route mounting."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import runtime
from .config import get_settings
from .db import close_pool, init_db
from .logging_config import configure_logging, request_id_var
from .routes import access, artifacts, chat, config, health, sessions

log = logging.getLogger(__name__)


async def _auto_ingest_if_empty(s) -> None:
    """Populate the knowledge base on first boot when it's empty. Runs in the
    background so startup (and the health check) aren't blocked by the long ingest."""
    try:
        from .rag.ingest import ingest
        from .repository import count_chunks

        if await count_chunks() > 0:
            log.info("auto-ingest: knowledge base already populated; skipping")
            return
        log.info("auto-ingest: empty KB — ingesting up to %s episode(s)…", s.ingest_max_episodes)
        await ingest(s.transcripts_repo, s.ingest_max_episodes, reset=True)
        log.info("auto-ingest: complete")
    except Exception:  # noqa: BLE001
        log.exception("auto-ingest failed; run `python -m app.rag.ingest` manually")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    log.info("Starting Lenny Growth Assistant (provider=%s, agent=%s)", s.llm_provider, s.agent_backend)
    try:
        await init_db()
    except Exception:  # noqa: BLE001 — stay up so /health can report the failure
        log.exception("init_db failed at startup; /health will show db degraded")
    if s.auto_ingest:
        asyncio.create_task(_auto_ingest_if_empty(s))
    yield
    await close_pool()


_settings = get_settings()
# Hide the interactive API docs / OpenAPI schema outside local dev so a public
# deployment doesn't advertise its full surface.
_docs_enabled = _settings.app_env == "local"
app = FastAPI(
    title="The Lenny Growth Assistant",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
_cors_kwargs: dict = dict(
    allow_origins=_settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# In local dev, Vite may pick any free port (5173, 5174, …) — allow any localhost origin
# so the SPA works regardless of which port it landed on.
if _settings.app_env == "local":
    _cors_kwargs["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
app.add_middleware(CORSMiddleware, **_cors_kwargs)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["x-request-id"] = rid
    return response


def _envelope(status_code: int, err_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"type": err_type, "message": message, "request_id": request_id_var.get()}},
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    return _envelope(422, "validation_error", str(exc.errors()))


@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    return _envelope(exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(Exception)
async def _generic_handler(request: Request, exc: Exception):
    log.exception("Unhandled error")
    return _envelope(500, "internal_error", str(exc))


app.include_router(health.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(artifacts.router)
app.include_router(config.router)
app.include_router(access.router)


# ---- Static SPA (bundled only in the single-service / Railway image) --------
# When the built frontend is baked into the image at app/static, FastAPI serves
# the SPA + /welcome on the SAME origin as the API — ideal for Cloudflare Access.
# In the docker-compose setup the SPA is served by the separate nginx `web`
# container, so this directory is absent and none of these routes activate.
_STATIC = Path(__file__).resolve().parent / "static"

if _STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_STATIC / "assets")), name="assets")


@app.get("/", include_in_schema=False)
async def root():
    if _STATIC.is_dir():
        return FileResponse(_STATIC / "index.html")
    return {
        "name": "The Lenny Growth Assistant",
        "provider": runtime.get_provider(),
        "model": runtime.active_model_label(),
        "health": "/health",
    }


if _STATIC.is_dir():

    @app.get("/welcome", include_in_schema=False)
    async def welcome_page():
        return FileResponse(_STATIC / "welcome.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Serve a real bundled file if it exists, else the SPA entry (client routing).
        target = (_STATIC / full_path).resolve()
        if full_path and str(target).startswith(str(_STATIC)) and target.is_file():
            return FileResponse(target)
        return FileResponse(_STATIC / "index.html")
