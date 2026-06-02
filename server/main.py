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

    # Serve run artifacts (screenshots) so the UI can show them.
    runs_dir = os.environ.get("RUNS_DIR", "runs")
    os.makedirs(runs_dir, exist_ok=True)
    app.mount("/artifacts", StaticFiles(directory=runs_dir), name="artifacts")

    # Serve the built frontend if present (web/dist). API routes are registered first,
    # so this catch-all only handles non-API paths.
    dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "dist")
    if os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")


@app.on_event("shutdown")
def _shutdown() -> None:
    stop_scheduler()
