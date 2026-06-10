"""Unit tests for the runner's queue-position and cancellation bookkeeping."""

from __future__ import annotations

from server import runner
from server.models import Run

from conftest import USER_A


def _queued(db, **kw) -> Run:
    run = Run(user_id=USER_A.id, status="queued", trigger="manual", steps=[], **kw)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_queue_position_counts_runs_ahead(db):
    r1 = _queued(db)
    r2 = _queued(db)
    r3 = _queued(db)
    assert runner.queue_position(db, r1.id) == 0
    assert runner.queue_position(db, r2.id) == 1
    assert runner.queue_position(db, r3.id) == 2


def test_queue_position_none_when_not_queued(db):
    r = _queued(db)
    r.status = "done"
    db.commit()
    assert runner.queue_position(db, r.id) is None


def test_request_cancel_queued_without_future_finalizes(db):
    r = _queued(db)
    # No live future registered (e.g. after a restart) -> finalize immediately.
    result = runner.request_cancel(db, r)
    db.refresh(r)
    assert result == "cancelled"
    assert r.status == "cancelled"
    assert r.finished_at is not None


def test_request_cancel_running_is_cooperative(db):
    r = _queued(db)
    r.status = "running"
    db.commit()
    result = runner.request_cancel(db, r)
    # Running runs can't be killed outright; they stop at the next step.
    assert result == "cancelling"
    assert runner._is_cancelled(r.id) is True


def test_request_cancel_finished_is_noop(db):
    r = _queued(db)
    r.status = "done"
    db.commit()
    assert runner.request_cancel(db, r) == "noop"
