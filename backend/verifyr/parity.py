"""Phase 1 orchestrator: run a web-to-mobile parity check end to end.

Flow (driven by the config flags in prompt1.json):
  1. Resolve the source-of-truth WEB value (selector or VLM extraction).
  2. Run the cheap API check (if api_check_first and an endpoint is configured).
  3. Decide whether to escalate to the device:
       - API value missing/unavailable      -> drive device (UI is the only signal)
       - API matches web                     -> drive device (confirm rendering)
       - API does NOT match web (backend old)-> skip device (it would just be old)
  4. If driving: navigate with the Phase 0 agent, read the app UI value, run the
     verifier (web vs app). On a stale-looking mismatch, perform the configured
     stale_retry_actions once and re-read.
  5. Classify the propagation outcome from all signals and store the result.

Usage:
  python parity.py --check "Summer Tote price"
  python parity.py --all
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re

from .agent import Agent
from .api_check import fetch_api_value
from .buildinfo import installed_build, version_lt
from .checks import Check, get_check, load_checks
from .classifier import classify
from .config import PromptConfig, Settings, load_all
from .device import Device, DeviceError
from .login import perform_login
from .reporting import Reporter
from .verifier import verify
from .vlm import VLMClient, get_vlm
from .web_extractor import resolve_web_value_for_check


def _now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _norm(value: str | None) -> str | None:
    """Normalize a value for loose equality (drop currency/whitespace/case)."""
    if value is None:
        return None
    return re.sub(r"[^0-9a-z]", "", value.lower())


def _loose_match(a: str | None, b: str | None) -> bool:
    na, nb = _norm(a), _norm(b)
    if na is None or nb is None:
        return False
    return na == nb


def run_check(
    check: Check,
    prompt: PromptConfig,
    settings: Settings,
    vlm: VLMClient,
    out_dir: str,
    verbose: bool = True,
    reporter: Reporter | None = None,
) -> dict:
    reporter = reporter or Reporter()

    def log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    log(f"\n========== CHECK: {check.name} ==========")
    target = check.android_target()
    label = target.label if target else check.name

    signals: dict = {
        "check_name": check.name,
        "label": label,
        "web_value": None,
        "api_value": None,
        "app_ui_value": None,
        "verifier_result": None,
        "rendering_broken": None,
        "installed_build": None,
        "requires_build": target.requires_build if target else None,
        "stale_retry_done": False,
    }
    detail: dict = {"notes": {}}

    # --- 1. web value (source of truth) ---
    web_value, web_note = resolve_web_value_for_check(check, vlm, prompt)
    signals["web_value"] = web_value
    detail["notes"]["web"] = web_note
    log(f"[web]  {web_value!r}  ({web_note})")
    reporter.emit("signal", {"name": "web_value", "value": web_value, "note": web_note})

    # --- 2. API check (cheap, first) ---
    if prompt.api_check_first and check.api.endpoint:
        api_res = fetch_api_value(check.api.endpoint, check.api.json_path, check.api.headers)
        signals["api_value"] = api_res.value
        detail["notes"]["api"] = api_res.error or "ok"
        log(f"[api]  {api_res.value!r}  ({api_res.error or 'ok'})")
        reporter.emit("signal", {"name": "api_value", "value": api_res.value, "note": api_res.error or "ok"})
    else:
        detail["notes"]["api"] = "skipped (no endpoint or api_check_first off)"
        log("[api]  skipped")

    # --- 3. escalation decision ---
    api_value = signals["api_value"]
    backend_is_old = api_value is not None and web_value is not None and not _loose_match(api_value, web_value)
    drive_device = prompt.escalate_to_device and target is not None and not backend_is_old
    if backend_is_old:
        log("[route] API differs from web -> backend is stale; skipping device.")
    elif not target:
        log("[route] no android app target -> cannot drive device.")
    elif not prompt.escalate_to_device:
        log("[route] escalate_to_device is off -> skipping device.")

    # --- 4. device capture (+ stale retry) ---
    if drive_device:
        target_settings = dataclasses.replace(
            settings, app_package=target.package or settings.app_package, app_path=None
        )
        device = Device(target_settings)
        try:
            device.connect()
        except DeviceError as err:
            detail["notes"]["device"] = str(err)
            log(f"[device] connect failed: {err}")
            device = None

        if device is not None:
            try:
                signals["installed_build"] = installed_build(
                    target.package or settings.app_package, settings.udid
                )
                log(f"[build] installed={signals['installed_build']} requires={signals['requires_build']}")

                if target_settings.login_flow:
                    lr = perform_login(device, target_settings, verbose)
                    detail["notes"]["login"] = lr.detail
                    log(f"[login] {'ok' if lr.ok else 'FAILED'} - {lr.detail}")

                agent = Agent(prompt, target_settings, vlm, device, verbose=verbose, reporter=reporter)
                run = agent.run(target.goal, web_value, run_root=os.path.join(out_dir, "agent-1"))
                signals["app_ui_value"] = run.asserted_value
                if run.verifier:
                    signals["verifier_result"] = run.verifier.get("result")
                    signals["rendering_broken"] = run.verifier.get("rendering_broken")
                detail["agent_run_1"] = run.run_dir

                # Stale retry: backend agrees with web but the UI mismatched.
                mismatch = signals["verifier_result"] == "mismatch"
                if mismatch and prompt.retry_on_stale and not signals["stale_retry_done"]:
                    log(f"[stale] mismatch -> retrying with {prompt.stale_retry_actions}")
                    _do_stale_retry(device, prompt.stale_retry_actions, target.package or settings.app_package)
                    # A relaunch logs us out, so re-run the login pre-step.
                    if target_settings.login_flow and "relaunch_app" in prompt.stale_retry_actions:
                        perform_login(device, target_settings, verbose)
                    signals["stale_retry_done"] = True
                    run2 = agent.run(target.goal, web_value, run_root=os.path.join(out_dir, "agent-2"))  # same agent (has reporter)
                    signals["app_ui_value"] = run2.asserted_value
                    if run2.verifier:
                        signals["verifier_result"] = run2.verifier.get("result")
                        signals["rendering_broken"] = run2.verifier.get("rendering_broken")
                    detail["agent_run_2"] = run2.run_dir
            finally:
                device.quit()

    # --- 5. classify + store ---
    reporter.emit("signals_final", dict(signals))
    classification = classify(vlm, prompt, signals)
    log(f"\n[VERDICT] {classification['verdict']}  (confidence {classification['confidence']})")
    log(f"  {classification['summary']}")
    log(f"  -> {classification['recommended_action']}")
    reporter.emit("verdict", classification)

    result = {"check": check.name, "signals": signals, "classification": classification, "detail": detail}
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", check.name)
    with open(os.path.join(out_dir, f"{safe_name}.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def _do_stale_retry(device: Device, actions: list, package: str | None) -> None:
    for action in actions:
        if action == "relaunch_app":
            device.relaunch_app(package)
            device.wait(2, "settle after relaunch")
        elif action == "pull_to_refresh":
            device.pull_to_refresh()
            device.wait(2, "settle after refresh")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifyr Phase 1 parity checker")
    parser.add_argument("--check", default=None, help="Name of a single check to run")
    parser.add_argument("--all", action="store_true", help="Run all checks in the file")
    parser.add_argument("--checks", default="checks.json", help="Path to the checks store")
    parser.add_argument("--prompt", default=None, help="Path to prompt1.json")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-step logs")
    args = parser.parse_args()

    prompt, settings = load_all(args.prompt)
    if not prompt.is_phase1:
        raise SystemExit(
            "Loaded prompt is not a Phase 1 config (no classifier). "
            "Ensure prompt1.json is present or pass --prompt idea/prompt1.json."
        )
    issues = settings.validate_for_run()
    if issues:
        raise SystemExit("Cannot run:\n  - " + "\n  - ".join(issues))

    checks = load_checks(args.checks)
    if args.all:
        selected = checks
    elif args.check:
        selected = [get_check(checks, args.check)]
    else:
        raise SystemExit("Pass --check NAME or --all")

    vlm = get_vlm(settings)
    out_dir = os.path.join(settings.runs_dir, "parity-" + _now_stamp())
    os.makedirs(out_dir, exist_ok=True)

    results = []
    for check in selected:
        try:
            results.append(run_check(check, prompt, settings, vlm, out_dir, verbose=not args.quiet))
        except Exception as err:  # one bad check shouldn't kill the batch
            print(f"check {check.name!r} errored: {err}")
            results.append({"check": check.name, "error": str(err)})

    # Summary table.
    print("\n\n================= PARITY SUMMARY =================")
    header = f"{'Check':<34} {'Verdict':<26} {'Conf':>5}"
    print(header)
    print("-" * len(header))
    for r in results:
        if "error" in r:
            print(f"{r['check'][:33]:<34} {'ERROR':<26} {'-':>5}")
            continue
        c = r["classification"]
        print(f"{r['check'][:33]:<34} {c['verdict']:<26} {c['confidence']:>5.2f}")

    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nResults written to {out_dir}/")


if __name__ == "__main__":
    main()
