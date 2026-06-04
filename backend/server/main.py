"""FastAPI application entry point.

Run with:  uvicorn server.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .events import bus
from .routers import apks, auth, checks, runs
from .scheduler import start as start_scheduler, stop as stop_scheduler
from .settings import server_settings

app = FastAPI(title="Verifyr", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=server_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(apks.router)
app.include_router(checks.router)
app.include_router(runs.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "verifyr"}


@app.on_event("startup")
def _startup() -> None:
    init_db()
    bus.bind_loop(asyncio.get_event_loop())
    start_scheduler()

    from verifyr.config import PROJECT_ROOT

    # Run screenshots are served per-user through GET /api/runs/{id}/artifact
    # (ownership-checked) — not via an open static mount.
    runs_dir = os.environ.get("RUNS_DIR", str(PROJECT_ROOT / "runs"))
    os.makedirs(runs_dir, exist_ok=True)

    # Serve the built frontend if present (frontend/dist). API routes are registered
    # first, so this catch-all only handles non-API paths.
    dist = PROJECT_ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")


@app.on_event("shutdown")
def _shutdown() -> None:
    stop_scheduler()
