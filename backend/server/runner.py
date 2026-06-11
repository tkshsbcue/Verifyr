"""Background runner: executes a parity check in a worker thread, persists the
Run, and streams events to WebSocket subscribers via the event bus.

A single worker (max_workers=1) serializes runs because there is one emulator.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

from verifyr import parity
from verifyr.agent import RunCancelled
from verifyr.checks import Check as EngineCheck
from verifyr.config import load_all
from verifyr.reporting import CallbackReporter
from verifyr.vlm import get_vlm

from . import supabase_client
from .db import SessionLocal
from .events import bus
from .models import Apk, Check, Run
from .settings import server_settings

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="verifyr-run")

# Cancellation registry. There is one shared worker, so runs queue FIFO and we
# track each in-flight run's Future (to cancel before it starts) plus a
# threading.Event the engine polls cooperatively (to stop a running one).
_registry_lock = threading.Lock()
_cancel_events: dict[int, threading.Event] = {}
_futures: dict[int, Future] = {}


def _register(run_id: int, future: Future) -> threading.Event:
    ev = threading.Event()
    with _registry_lock:
        _cancel_events[run_id] = ev
        _futures[run_id] = future
    return ev


def _unregister(run_id: int) -> None:
    with _registry_lock:
        _cancel_events.pop(run_id, None)
        _futures.pop(run_id, None)


def _is_cancelled(run_id: int) -> bool:
    ev = _cancel_events.get(run_id)
    return ev is not None and ev.is_set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def queue_position(db, run_id: int) -> int | None:
    """How many runs are ahead of this one in the shared single-worker queue.

    0 means it's next (or already running); None means it isn't queued. Counts
    every still-pending/running run across all users, since one device serializes
    them all.
    """
    run = db.get(Run, run_id)
    if run is None or run.status != "queued":
        return None
    ahead = (
        db.query(Run)
        .filter(Run.status.in_(("queued", "running")), Run.id < run_id)
        .count()
    )
    return ahead


def request_cancel(db, run: Run) -> str:
    """Cancel a queued or running run.

    Returns one of: ``cancelled`` (was still queued — stopped immediately),
    ``cancelling`` (was running — will stop at the next step), or ``noop``
    (already finished).
    """
    if run.status in ("done", "error", "cancelled"):
        return "noop"

    # Ensure a cancel flag exists and set it. The engine reads the registry live
    # via _is_cancelled(), so setting it here stops a running engine at its next
    # step even if the run wasn't pre-registered (e.g. after a restart).
    with _registry_lock:
        ev = _cancel_events.get(run.id)
        if ev is None:
            ev = threading.Event()
            _cancel_events[run.id] = ev
        future = _futures.get(run.id)
    ev.set()

    # If the worker hasn't picked it up yet, cancel the Future outright and
    # finalize here — _execute will never run for it.
    if future is not None and future.cancel():
        _finalize_cancelled(run.id)
        return "cancelled"

    if run.status == "queued":
        # No live future (e.g. after a restart) but still queued — finalize now.
        _finalize_cancelled(run.id)
        return "cancelled"
    return "cancelling"


def _finalize_cancelled(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        if run is None or run.status in ("done", "error", "cancelled"):
            return
        run.status = "cancelled"
        run.error = "Cancelled by user."
        run.finished_at = _utcnow()
        db.commit()
        bus.publish_threadsafe(
            run_id, {"type": "done", "status": "cancelled", "verdict": None, "error": run.error}
        )
    finally:
        _unregister(run_id)
        db.close()


def enqueue_run(db, check_id: int, trigger: str = "manual") -> Run:
    """Create a queued Run row and submit it to the worker. Returns the Run.

    The run inherits the owning check's user_id so scheduled runs (which have no
    request context) stay attributed to the right user.
    """
    check = db.get(Check, check_id)
    run = Run(
        check_id=check_id,
        user_id=check.user_id if check else None,
        status="queued",
        trigger=trigger,
        steps=[],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    future = _executor.submit(_execute, run.id)
    _register(run.id, future)
    return run


def enqueue_quick_run(db, apk_id: int, goal: str, name: str | None, web: dict, user_id: str) -> Run:
    """Create an ad-hoc 'quick test' run: an uploaded APK + a typed prompt."""
    run = Run(
        user_id=user_id,
        apk_id=apk_id,
        goal=goal,
        name=name or "Quick test",
        config={k: v for k, v in web.items() if v},
        status="queued",
        trigger="quick",
        steps=[],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    future = _executor.submit(_execute_quick, run.id)
    _register(run.id, future)
    return run


def _artifact_url(path: str | None, out_dir: str, run_id: int, user_id: str) -> str | None:
    """Mirror a screenshot to per-user Supabase Storage and return a stable,
    ownership-checked proxy URL (served by GET /api/runs/{id}/artifact)."""
    if not path or not out_dir:
        return None
    try:
        rel = os.path.relpath(path, out_dir)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    # Best-effort upload; the proxy falls back to the local copy if it fails.
    try:
        with open(path, "rb") as fh:
            supabase_client.upload_artifact(f"{user_id}/{run_id}/{rel}", fh.read())
    except OSError:
        pass
    return f"/api/runs/{run_id}/artifact?file={quote(rel)}"


def _execute(run_id: int) -> None:
    db = SessionLocal()
    run = db.get(Run, run_id)
    if run is None:
        db.close()
        return

    # Cancelled while still queued (before the worker picked it up).
    if _is_cancelled(run_id):
        run.status = "cancelled"
        run.error = "Cancelled by user."
        run.finished_at = _utcnow()
        db.commit()
        bus.publish_threadsafe(run_id, {"type": "done", "status": "cancelled", "verdict": None, "error": run.error})
        _unregister(run_id)
        db.close()
        return

    check_name = run.check.name
    check_config = dict(run.check.config or {})
    user_id = run.user_id
    run.status = "running"
    db.commit()
    bus.publish_threadsafe(run_id, {"type": "status", "status": "running"})

    steps: list = []

    try:
        prompt, settings = load_all()
        runs_dir = settings.runs_dir

        out_dir = os.path.join(runs_dir, f"server-run-{run_id}")
        os.makedirs(out_dir, exist_ok=True)
        run.out_dir = out_dir
        db.commit()

        def on_event(kind: str, data: dict) -> None:
            payload = {"type": kind, **data}
            if "screenshot" in payload:
                payload["screenshot_url"] = _artifact_url(payload.get("screenshot"), out_dir, run_id, user_id)
            if kind == "step":
                steps.append(payload)
                run.steps = list(steps)
                db.commit()
            bus.publish_threadsafe(run_id, payload)

        reporter = CallbackReporter(on_event)
        vlm = get_vlm(settings)
        engine_check = EngineCheck.from_dict({"name": check_name, **check_config})

        result = parity.run_check(
            engine_check, prompt, settings, vlm, out_dir, verbose=False, reporter=reporter,
            should_cancel=lambda: _is_cancelled(run_id),
        )
        cls = result["classification"]
        run.status = "done"
        run.verdict = cls.get("verdict")
        run.confidence = cls.get("confidence")
        run.summary = cls.get("summary")
        run.recommended_action = cls.get("recommended_action")
        run.signals = result.get("signals")
        run.detail = result.get("detail")
    except RunCancelled:
        run.status = "cancelled"
        run.error = "Cancelled by user."
    except Exception as err:  # never crash the worker
        run.status = "error"
        run.error = f"{type(err).__name__}: {err}"
    finally:
        run.finished_at = _utcnow()
        db.commit()
        bus.publish_threadsafe(
            run_id, {"type": "done", "status": run.status, "verdict": run.verdict, "error": run.error}
        )
        try:
            _maybe_alert(db, run)
        except Exception:
            pass
        _unregister(run_id)
        db.close()


def _execute_quick(run_id: int) -> None:
    """Run the Phase-0 agent on an uploaded APK with a free-text goal."""
    import dataclasses

    from verifyr.agent import Agent
    from verifyr.device import Device
    from verifyr.verifier import resolve_web_value

    db = SessionLocal()
    run = db.get(Run, run_id)
    if run is None:
        db.close()
        return
    if _is_cancelled(run_id):
        run.status = "cancelled"
        run.error = "Cancelled by user."
        run.finished_at = _utcnow()
        db.commit()
        bus.publish_threadsafe(run_id, {"type": "done", "status": "cancelled", "verdict": None, "error": run.error})
        _unregister(run_id)
        db.close()
        return

    apk = db.get(Apk, run.apk_id) if run.apk_id else None
    goal = run.goal or ""
    cfg = run.config or {}
    user_id = run.user_id

    run.status = "running"
    db.commit()
    bus.publish_threadsafe(run_id, {"type": "status", "status": "running"})

    steps: list = []
    device = None
    try:
        prompt, settings = load_all()
        runs_dir = settings.runs_dir
        # Point the engine at the uploaded APK; Appium installs it and launches the
        # main activity automatically. No login pre-step for ad-hoc tests.
        settings = dataclasses.replace(
            settings,
            app_path=(apk.path if apk else settings.app_path),
            app_package=None,
            app_activity=None,
            login_flow=None,
        )

        out_dir = os.path.join(runs_dir, f"server-run-{run_id}")
        os.makedirs(out_dir, exist_ok=True)
        run.out_dir = out_dir
        db.commit()

        def on_event(kind: str, data: dict) -> None:
            payload = {"type": kind, **data}
            if "screenshot" in payload:
                payload["screenshot_url"] = _artifact_url(payload.get("screenshot"), out_dir, run_id, user_id)
            if kind == "step":
                steps.append(payload)
                run.steps = list(steps)
                db.commit()
            bus.publish_threadsafe(run_id, payload)

        reporter = CallbackReporter(on_event)

        # Optional source-of-truth (literal value or captured from a URL).
        web_value = None
        try:
            web_value = resolve_web_value(
                cfg.get("web_value"), cfg.get("web_url"), cfg.get("web_selector"), cfg.get("web_attribute")
            )
        except Exception as err:
            bus.publish_threadsafe(run_id, {"type": "signal", "name": "web_value", "value": f"(capture failed: {err})"})
        if web_value is not None:
            bus.publish_threadsafe(run_id, {"type": "signal", "name": "web_value", "value": web_value})

        vlm = get_vlm(settings)

        device = Device(settings)
        device.connect()
        agent = Agent(
            prompt, settings, vlm, device, verbose=False, reporter=reporter,
            should_cancel=lambda: _is_cancelled(run_id),
        )
        result = agent.run(goal, web_value, run_root=os.path.join(out_dir, "agent-1"))

        run.status = "done"
        run.verdict = result.status  # pass | fail | blocked | stuck | error | budget-exhausted
        run.summary = result.reason
        run.signals = {
            "app_ui_value": result.asserted_value,
            "web_value": web_value,
            "verifier_result": (result.verifier or {}).get("result"),
            "rendering_broken": (result.verifier or {}).get("rendering_broken"),
        }
        if result.verifier:
            run.confidence = result.verifier.get("confidence")
        run.detail = {"agent_status": result.status, "model_verdict": result.verdict}
        bus.publish_threadsafe(
            run_id,
            {
                "type": "verdict",
                "verdict": run.verdict,
                "confidence": run.confidence or 0.0,
                "summary": result.reason,
                "recommended_action": "",
            },
        )
    except RunCancelled:
        run.status = "cancelled"
        run.error = "Cancelled by user."
    except Exception as err:
        run.status = "error"
        run.error = f"{type(err).__name__}: {err}"
    finally:
        if device is not None:
            device.quit()
        run.finished_at = _utcnow()
        db.commit()
        bus.publish_threadsafe(
            run_id, {"type": "done", "status": run.status, "verdict": run.verdict, "error": run.error}
        )
        _unregister(run_id)
        db.close()


def _maybe_alert(db, run: Run) -> None:
    """Alert when a (scheduled) check regresses to a non-pass verdict."""
    if run.status != "done" or not run.verdict or run.verdict == "pass":
        return
    prev = (
        db.query(Run)
        .filter(Run.check_id == run.check_id, Run.id < run.id, Run.status == "done")
        .order_by(Run.id.desc())
        .first()
    )
    # Alert on a new/changed failure (avoid spamming the same standing failure).
    if prev is not None and prev.verdict == run.verdict:
        return

    check = run.check
    subject = f"[Verifyr] {check.name}: {run.verdict}"
    body = f"{run.summary}\n\nRecommended: {run.recommended_action}\nRun #{run.id}"
    _send_alert(check.alert_email, subject, body)
    run.alerted = True
    db.commit()


def _send_alert(to_email: str | None, subject: str, body: str) -> None:
    if not to_email or not server_settings.smtp_host:
        print(f"[ALERT] {subject}\n{body}", flush=True)
        return
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = server_settings.alert_from
    msg["To"] = to_email
    try:
        with smtplib.SMTP(server_settings.smtp_host, server_settings.smtp_port) as s:
            s.starttls()
            if server_settings.smtp_user:
                s.login(server_settings.smtp_user, server_settings.smtp_password or "")
            s.send_message(msg)
    except Exception as err:
        print(f"[ALERT] SMTP failed ({err}); message was:\n{subject}\n{body}", flush=True)
