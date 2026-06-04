"""Run listing/detail, the per-user artifact proxy, and the live WebSocket stream."""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..deps import current_user
from ..events import bus
from ..models import Apk, Run
from ..runner import enqueue_quick_run
from ..schemas import QuickRunCreate, RunOut, RunSummary
from ..supabase_client import SupaUser, signed_url, verify_token

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _owned_run(db: Session, run_id: int, user_id: str) -> Run:
    run = db.get(Run, run_id)
    if not run or run.user_id != user_id:
        raise HTTPException(404, "Run not found")
    return run


@router.post("/quick", response_model=RunSummary, status_code=202)
def quick_run(payload: QuickRunCreate, db: Session = Depends(get_db), user: SupaUser = Depends(current_user)):
    apk = db.get(Apk, payload.apk_id)
    if not apk or apk.user_id != user.id:
        raise HTTPException(404, "APK not found — upload one first")
    if not payload.goal.strip():
        raise HTTPException(400, "Goal (prompt) is required")
    return enqueue_quick_run(
        db,
        payload.apk_id,
        payload.goal,
        payload.name,
        {
            "web_value": payload.web_value,
            "web_url": payload.web_url,
            "web_selector": payload.web_selector,
            "web_attribute": payload.web_attribute,
        },
        user_id=user.id,
    )


@router.get("", response_model=list[RunSummary])
def list_runs(
    check_id: int | None = Query(None),
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
    user: SupaUser = Depends(current_user),
):
    q = db.query(Run).filter(Run.user_id == user.id)
    if check_id is not None:
        q = q.filter(Run.check_id == check_id)
    return q.order_by(Run.id.desc()).limit(limit).all()


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db), user: SupaUser = Depends(current_user)):
    return _owned_run(db, run_id, user.id)


@router.get("/{run_id}/artifact")
def get_artifact(
    run_id: int,
    file: str = Query(..., description="path of the screenshot relative to the run's out_dir"),
    token: str | None = Query(None),
):
    """Serve a run screenshot, gated by ownership.

    <img> tags can't send an Authorization header, so the access token is
    passed as a query parameter (same pattern as the WebSocket stream). The
    image is served from Supabase Storage via a short-lived signed URL, falling
    back to the locally stored copy.
    """
    user = verify_token(token)
    if user is None:
        raise HTTPException(401, "Not authenticated")

    # Reject path traversal before touching the filesystem / storage key.
    rel = os.path.normpath(file)
    if rel.startswith("..") or os.path.isabs(rel):
        raise HTTPException(400, "Invalid path")

    db = SessionLocal()
    try:
        run = _owned_run(db, run_id, user.id)
        out_dir = run.out_dir
    finally:
        db.close()

    url = signed_url(f"{user.id}/{run_id}/{rel}")
    if url:
        return RedirectResponse(url)

    # Storage unavailable — fall back to the locally stored copy.
    if out_dir:
        local = os.path.normpath(os.path.join(out_dir, rel))
        if local.startswith(os.path.normpath(out_dir)) and os.path.isfile(local):
            return FileResponse(local)
    raise HTTPException(404, "Artifact not found")


@router.websocket("/{run_id}/stream")
async def stream(websocket: WebSocket, run_id: int, token: str | None = Query(None)):
    # WebSockets can't send Authorization headers from the browser, so auth via ?token=.
    user = await asyncio.to_thread(verify_token, token)
    if user is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()

    # Send a snapshot first so a late subscriber catches up.
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        if run is None or run.user_id != user.id:
            await websocket.send_json({"type": "error", "message": "run not found"})
            await websocket.close()
            return
        await websocket.send_json(
            {"type": "snapshot", "status": run.status, "verdict": run.verdict, "steps": run.steps or []}
        )
        already_done = run.status in ("done", "error")
    finally:
        db.close()

    if already_done:
        await websocket.close()
        return

    q = bus.subscribe(run_id)
    try:
        while True:
            event = await q.get()
            await websocket.send_json(event)
            if event.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(run_id, q)
        try:
            await websocket.close()
        except Exception:
            pass
