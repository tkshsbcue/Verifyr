"""Background runner: executes a parity check in a worker thread, persists the
Run, and streams events to WebSocket subscribers via the event bus.

A single worker (max_workers=1) serializes runs because there is one emulator.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from verifyr import parity
from verifyr.checks import Check as EngineCheck
from verifyr.config import load_all
from verifyr.reporting import CallbackReporter
from verifyr.vlm import get_vlm

from .db import SessionLocal
from .events import bus
from .models import Apk, Run
from .settings import server_settings

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="verifyr-run")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_run(db, check_id: int, trigger: str = "manual") -> Run:
    """Create a queued Run row and submit it to the worker. Returns the Run."""
    run = Run(check_id=check_id, status="queued", trigger=trigger, steps=[])
    db.add(run)
    db.commit()
    db.refresh(run)
    _executor.submit(_execute, run.id)
    return run


def enqueue_quick_run(db, apk_id: int, goal: str, name: str | None, web: dict) -> Run:
    """Create an ad-hoc 'quick test' run: an uploaded APK + a typed prompt."""
    run = Run(
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
    _executor.submit(_execute_quick, run.id)
    return run


def _artifact_url(path: str | None, runs_dir: str) -> str | None:
    if not path:
        return None
    try:
        rel = os.path.relpath(path, runs_dir)
    except ValueError:
        return None
    return f"/artifacts/{rel}"


def _execute(run_id: int) -> None:
    db = SessionLocal()
    run = db.get(Run, run_id)
    if run is None:
        db.close()
        return

    check_name = run.check.name
    check_config = dict(run.check.config or {})
    run.status = "running"
    db.commit()
    bus.publish_threadsafe(run_id, {"type": "status", "status": "running"})

    steps: list = []

    try:
        prompt, settings = load_all()
        runs_dir = settings.runs_dir

        def on_event(kind: str, data: dict) -> None:
            payload = {"type": kind, **data}
            if "screenshot" in payload:
                payload["screenshot_url"] = _artifact_url(payload.get("screenshot"), runs_dir)
            if kind == "step":
                steps.append(payload)
                run.steps = list(steps)
                db.commit()
            bus.publish_threadsafe(run_id, payload)

        reporter = CallbackReporter(on_event)
        vlm = get_vlm(settings)
        engine_check = EngineCheck.from_dict({"name": check_name, **check_config})

        out_dir = os.path.join(runs_dir, f"server-run-{run_id}")
        os.makedirs(out_dir, exist_ok=True)
        run.out_dir = out_dir
        db.commit()

        result = parity.run_check(
            engine_check, prompt, settings, vlm, out_dir, verbose=False, reporter=reporter
        )
        cls = result["classification"]
        run.status = "done"
        run.verdict = cls.get("verdict")
        run.confidence = cls.get("confidence")
        run.summary = cls.get("summary")
        run.recommended_action = cls.get("recommended_action")
        run.signals = result.get("signals")
        run.detail = result.get("detail")
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
    apk = db.get(Apk, run.apk_id) if run.apk_id else None
    goal = run.goal or ""
    cfg = run.config or {}

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

        def on_event(kind: str, data: dict) -> None:
            payload = {"type": kind, **data}
            if "screenshot" in payload:
                payload["screenshot_url"] = _artifact_url(payload.get("screenshot"), runs_dir)
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
        out_dir = os.path.join(runs_dir, f"server-run-{run_id}")
        os.makedirs(out_dir, exist_ok=True)
        run.out_dir = out_dir
        db.commit()

        device = Device(settings)
        device.connect()
        agent = Agent(prompt, settings, vlm, device, verbose=False, reporter=reporter)
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
