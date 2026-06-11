"""Checks CRUD + trigger-a-run. All rows are scoped to the current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import Check, Run
from ..runner import enqueue_run
from ..schemas import CheckCreate, CheckOut, CheckUpdate, RunSummary
from ..supabase_client import SupaUser
from .. import scheduler as sched

router = APIRouter(prefix="/api/checks", tags=["checks"])


def _owned_check(db: Session, check_id: int, user: SupaUser) -> Check:
    check = db.get(Check, check_id)
    if not check or check.user_id != user.id:
        raise HTTPException(404, "Check not found")
    return check


def _to_out(db: Session, check: Check) -> CheckOut:
    last = (
        db.query(Run)
        .filter(Run.check_id == check.id, Run.status == "done")
        .order_by(Run.id.desc())
        .first()
    )
    out = CheckOut.model_validate(check)
    out.last_verdict = last.verdict if last else None
    return out


@router.get("", response_model=list[CheckOut])
def list_checks(db: Session = Depends(get_db), user: SupaUser = Depends(current_user)):
    rows = db.query(Check).filter(Check.user_id == user.id).order_by(Check.name).all()
    return [_to_out(db, c) for c in rows]


@router.post("", response_model=CheckOut, status_code=201)
def create_check(payload: CheckCreate, db: Session = Depends(get_db), user: SupaUser = Depends(current_user)):
    check = Check(
        user_id=user.id,
        name=payload.name,
        config=payload.config.model_dump(),
        schedule=payload.schedule,
        alert_email=payload.alert_email,
        enabled=payload.enabled,
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    sched.sync_jobs()
    return _to_out(db, check)


@router.get("/{check_id}", response_model=CheckOut)
def get_check(check_id: int, db: Session = Depends(get_db), user: SupaUser = Depends(current_user)):
    return _to_out(db, _owned_check(db, check_id, user))


@router.put("/{check_id}", response_model=CheckOut)
def update_check(
    check_id: int, payload: CheckUpdate, db: Session = Depends(get_db), user: SupaUser = Depends(current_user)
):
    check = _owned_check(db, check_id, user)
    data = payload.model_dump(exclude_unset=True)
    if "config" in data and data["config"] is not None:
        check.config = payload.config.model_dump()
    for field in ("name", "schedule", "alert_email", "enabled"):
        if field in data and data[field] is not None:
            setattr(check, field, data[field])
    db.commit()
    db.refresh(check)
    sched.sync_jobs()
    return _to_out(db, check)


@router.delete("/{check_id}", status_code=204)
def delete_check(check_id: int, db: Session = Depends(get_db), user: SupaUser = Depends(current_user)):
    check = _owned_check(db, check_id, user)
    db.delete(check)
    db.commit()
    sched.sync_jobs()


@router.post("/{check_id}/run", response_model=RunSummary, status_code=202)
def run_check_now(check_id: int, db: Session = Depends(get_db), user: SupaUser = Depends(current_user)):
    _owned_check(db, check_id, user)
    return enqueue_run(db, check_id, trigger="manual")
