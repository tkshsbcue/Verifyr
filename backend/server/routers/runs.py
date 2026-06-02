"""Run listing/detail + live WebSocket stream."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..deps import current_user
from ..events import bus
from ..models import Apk, Run, User
from ..runner import enqueue_quick_run
from ..schemas import QuickRunCreate, RunOut, RunSummary
from ..security import decode_token

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("/quick", response_model=RunSummary, status_code=202)
def quick_run(payload: QuickRunCreate, db: Session = Depends(get_db), _: User = Depends(current_user)):
    if not db.get(Apk, payload.apk_id):
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
    )


@router.get("", response_model=list[RunSummary])
def list_runs(
    check_id: int | None = Query(None),
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    q = db.query(Run)
    if check_id is not None:
        q = q.filter(Run.check_id == check_id)
    return q.order_by(Run.id.desc()).limit(limit).all()


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.websocket("/{run_id}/stream")
async def stream(websocket: WebSocket, run_id: int, token: str | None = Query(None)):
    # WebSockets can't send Authorization headers from the browser, so auth via ?token=.
    if not token or not decode_token(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()

    # Send a snapshot first so a late subscriber catches up.
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        if run is None:
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
