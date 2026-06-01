"""The autonomous agent loop.

Each step:
  1. Capture screenshot + accessibility tree from the device.
  2. Render the step_input_template (goal, step, max_steps, recent actions, tree)
     and send it with agent_system_prompt + the screenshot to the VLM.
  3. Parse the JSON action and execute it via device.py.
  4. Record the action, check reliability backstops, repeat until finish or budget.

Reliability backstops live here, not just in the prompt:
  - loop detection: hash the accessibility tree; stop after N unchanged steps.
  - error tolerance: catch every step's errors; stop after M consecutive errors.

Artifacts: a timestamped run folder with a screenshot per step and run.json.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from config import PromptConfig, Settings, load_all
from device import Device, DeviceError, ERR_ELEMENT_NOT_FOUND
from verifier import resolve_web_value, verify
from vlm import VLMClient, VLMError, get_vlm

# Outcome statuses recorded in run.json / used by the eval harness.
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_BLOCKED = "blocked"
STATUS_STUCK = "stuck"
STATUS_ERROR = "error"
STATUS_BUDGET = "budget-exhausted"

# Failure buckets for the eval tally.
FAIL_WRONG_TAP = "wrong-tap"
FAIL_LOOP = "loop"
FAIL_ELEMENT_NOT_FOUND = "element-not-found"
FAIL_VERIFIER_MISMATCH = "verifier-mismatch"


def _now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _tree_hash(tree: str) -> str:
    return hashlib.md5(tree.encode("utf-8")).hexdigest()


def _render(template: str, **values) -> str:
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", str(val))
    return out


def _format_recent(actions: list[dict], limit: int = 8) -> str:
    if not actions:
        return "(none yet)"
    recent = actions[-limit:]
    lines = []
    for a in recent:
        act = a.get("action", {})
        desc = act.get("type", "?")
        for k in ("target", "text", "direction", "verdict", "label"):
            if act.get(k):
                desc += f" {k}={act[k]}"
        outcome = a.get("result", {}).get("detail", "")
        lines.append(f"- step {a.get('step')}: {desc} -> {outcome}")
    return "\n".join(lines)


@dataclass
class RunResult:
    goal: str
    web_value: str | None
    status: str
    verdict: str | None
    reason: str
    steps_taken: int
    latency_seconds: float
    failure_reason: str | None
    verifier: dict | None
    run_dir: str
    trace: list[dict] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        prompt: PromptConfig,
        settings: Settings,
        vlm: VLMClient,
        device: Device,
        stuck_threshold: int = 3,
        max_consecutive_errors: int = 3,
        verbose: bool = True,
    ):
        self.prompt = prompt
        self.settings = settings
        self.vlm = vlm
        self.device = device
        self.stuck_threshold = stuck_threshold
        self.max_consecutive_errors = max_consecutive_errors
        self.verbose = verbose

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def run(self, goal: str, web_value: str | None = None, run_root: str | None = None) -> RunResult:
        max_steps = self.prompt.max_steps
        run_dir = run_root or os.path.join(self.settings.runs_dir, _now_stamp())
        os.makedirs(run_dir, exist_ok=True)

        trace: list[dict] = []
        recent: list[dict] = []
        start = time.time()

        last_hash: str | None = None
        unchanged_streak = 0
        consecutive_errors = 0

        status = STATUS_BUDGET
        verdict: str | None = None
        reason = "Step budget exhausted before the goal was reached."
        failure_reason: str | None = FAIL_LOOP
        verifier_out: dict | None = None

        self._log(f"\n=== GOAL: {goal} ===")
        self._log(f"run dir: {run_dir}  | budget: {max_steps} steps\n")

        for step in range(1, max_steps + 1):
            step_record: dict[str, Any] = {"step": step}
            try:
                screenshot_b64 = self.device.screenshot_b64()
                tree = self.device.accessibility_tree()
            except Exception as err:  # capture failed — count as a step error
                self._log(f"[step {step}] capture error: {err}")
                consecutive_errors += 1
                step_record.update({"error": f"capture failed: {err}"})
                trace.append(step_record)
                if consecutive_errors >= self.max_consecutive_errors:
                    status, reason, failure_reason = STATUS_ERROR, str(err), FAIL_WRONG_TAP
                    break
                continue

            # Save the screenshot for this step.
            shot_path = os.path.join(run_dir, f"step-{step:02d}.png")
            try:
                with open(shot_path, "wb") as fh:
                    fh.write(base64.b64decode(screenshot_b64))
            except Exception:
                shot_path = ""

            # --- loop detection backstop ---
            h = _tree_hash(tree)
            if h == last_hash:
                unchanged_streak += 1
            else:
                unchanged_streak = 0
            last_hash = h
            if unchanged_streak >= self.stuck_threshold:
                self._log(f"[step {step}] screen unchanged for {self.stuck_threshold} steps -> stuck")
                status, reason, failure_reason = (
                    STATUS_STUCK,
                    f"Screen unchanged for {self.stuck_threshold} consecutive steps.",
                    FAIL_LOOP,
                )
                step_record.update({"screenshot": shot_path, "note": "stuck"})
                trace.append(step_record)
                break

            # --- ask the model ---
            user_text = _render(
                self.prompt.step_input_template,
                goal=goal,
                step=step,
                max_steps=max_steps,
                recent_actions=_format_recent(recent),
                accessibility_tree=tree,
            )
            try:
                decision = self.vlm.complete(
                    self.prompt.agent_system_prompt,
                    user_text,
                    screenshot_b64 if self.prompt.use_screenshot else None,
                )
            except VLMError as err:
                self._log(f"[step {step}] model error: {err}")
                consecutive_errors += 1
                step_record.update({"screenshot": shot_path, "error": f"model: {err}"})
                trace.append(step_record)
                if consecutive_errors >= self.max_consecutive_errors:
                    status, reason, failure_reason = STATUS_ERROR, str(err), FAIL_WRONG_TAP
                    break
                continue

            action = decision.get("action", {}) or {}
            atype = action.get("type", "")
            observation = decision.get("observation", "")
            reasoning = decision.get("reasoning", "")
            self._log(f"[step {step}] {atype}: {action.get('target') or action.get('reason') or ''}")
            self._log(f"    obs: {observation}")
            self._log(f"    why: {reasoning}")

            step_record.update(
                {
                    "screenshot": shot_path,
                    "observation": observation,
                    "reasoning": reasoning,
                    "action": action,
                }
            )

            # --- execute ---
            result, terminal = self._execute(action, screenshot_b64, goal, web_value)
            step_record["result"] = asdict(result) if hasattr(result, "__dataclass_fields__") else result
            if isinstance(result, dict) and result.get("verifier"):
                verifier_out = result["verifier"]

            recent.append({"step": step, "action": action, "result": step_record["result"]})
            trace.append(step_record)

            # Track consecutive errors for non-terminal failed actions.
            ok = step_record["result"].get("ok", True) if isinstance(step_record["result"], dict) else True
            if ok:
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                if consecutive_errors >= self.max_consecutive_errors:
                    status = STATUS_ERROR
                    reason = "Too many consecutive failed actions."
                    err_cls = step_record["result"].get("error_class") if isinstance(step_record["result"], dict) else None
                    failure_reason = FAIL_ELEMENT_NOT_FOUND if err_cls == ERR_ELEMENT_NOT_FOUND else FAIL_WRONG_TAP
                    break

            if terminal:
                status = terminal["status"]
                verdict = terminal.get("verdict")
                reason = terminal.get("reason", "")
                failure_reason = terminal.get("failure_reason")
                if terminal.get("verifier"):
                    verifier_out = terminal["verifier"]
                break

        latency = time.time() - start
        steps_taken = len(trace)

        # Final pass/fail resolution, factoring in the verifier if present.
        status, failure_reason = self._resolve_outcome(status, verifier_out, failure_reason)

        run_result = RunResult(
            goal=goal,
            web_value=web_value,
            status=status,
            verdict=verdict,
            reason=reason,
            steps_taken=steps_taken,
            latency_seconds=round(latency, 2),
            failure_reason=failure_reason if status != STATUS_PASS else None,
            verifier=verifier_out,
            run_dir=run_dir,
            trace=trace,
        )

        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as fh:
            json.dump(asdict(run_result), fh, indent=2)

        self._log(
            f"\n--- RESULT: {status.upper()} in {steps_taken} steps, "
            f"{latency:.1f}s | {reason}"
        )
        if verifier_out:
            self._log(f"    verifier: {verifier_out.get('result')} ({verifier_out.get('confidence')}) "
                      f"- {verifier_out.get('explanation')}")
        return run_result

    def _execute(self, action: dict, screenshot_b64: str, goal: str, web_value: str | None):
        """Run one action. Returns (result, terminal_or_None).

        terminal is a dict describing how the run should end, or None to continue.
        result is an ActionResult-as-dict (so it serializes cleanly).
        """
        atype = action.get("type", "")
        dev = self.device

        if atype == "finish":
            verdict = action.get("verdict", "fail")
            reason = action.get("reason", "")
            status = {
                "pass": STATUS_PASS,
                "fail": STATUS_FAIL,
                "blocked": STATUS_BLOCKED,
            }.get(verdict, STATUS_FAIL)
            failure_reason = None if status == STATUS_PASS else FAIL_WRONG_TAP
            return {"ok": True, "detail": f"finish: {verdict}"}, {
                "status": status,
                "verdict": verdict,
                "reason": reason,
                "failure_reason": failure_reason,
            }

        if atype == "assert":
            label = action.get("label", "value")
            found_value = action.get("found_value", "")
            result_payload: dict[str, Any] = {"ok": True, "detail": f"asserted {label}={found_value!r}"}
            if web_value is not None:
                verifier_out = verify(self.vlm, self.prompt, label, found_value, web_value, screenshot_b64)
                result_payload["verifier"] = verifier_out
                result_payload["detail"] += f" | verifier={verifier_out.get('result')}"
            else:
                result_payload["detail"] += " | no web_value provided, skipped verification"
            return result_payload, None

        # Device-affecting actions.
        if atype == "tap":
            res = dev.tap(action.get("target"))
        elif atype == "type_text":
            res = dev.type_text(action.get("target"), action.get("text"))
        elif atype == "scroll":
            res = dev.scroll(action.get("direction", "down"))
        elif atype == "swipe":
            res = dev.swipe(action.get("direction", "up"))
        elif atype == "press_back":
            res = dev.press_back()
        elif atype == "wait":
            res = dev.wait(action.get("seconds"), action.get("reason", ""))
        else:
            return {"ok": False, "detail": f"unknown action type: {atype}", "error_class": "executor-error"}, None

        return asdict(res), None

    @staticmethod
    def _resolve_outcome(status: str, verifier_out: dict | None, failure_reason: str | None):
        """A pass with a verifier present only stands if the verifier matched."""
        if status == STATUS_PASS and verifier_out is not None:
            if verifier_out.get("result") != "match":
                return STATUS_FAIL, FAIL_VERIFIER_MISMATCH
        return status, failure_reason


def run_goal(
    goal: str,
    web_value: str | None = None,
    prompt_path: str | None = None,
    verbose: bool = True,
    web_url: str | None = None,
    web_selector: str | None = None,
    web_attribute: str | None = None,
    web_headed: bool = False,
) -> RunResult:
    """Convenience entry point: wire everything up and run one goal.

    The source-of-truth value is resolved from an explicit `web_value` or, if
    absent, captured live from `web_url` (+ optional selector) via Playwright.
    """
    prompt, settings = load_all(prompt_path)
    issues = settings.validate_for_run()
    if issues:
        raise SystemExit("Cannot run:\n  - " + "\n  - ".join(issues))

    web_value = resolve_web_value(
        web_value, web_url, web_selector, web_attribute, headless=not web_headed
    )
    if verbose and web_value is not None:
        src = "url" if web_url and web_value else "flag"
        print(f"source-of-truth value ({src}): {web_value!r}")

    vlm = get_vlm(settings)
    device = Device(settings)
    device.connect()
    try:
        agent = Agent(prompt, settings, vlm, device, verbose=verbose)
        return agent.run(goal, web_value)
    finally:
        device.quit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifyr Phase 0 mobile QA agent")
    parser.add_argument("--goal", required=True, help="High-level goal for the agent")
    parser.add_argument("--web-value", default=None, help="Source-of-truth value for parity verification")
    parser.add_argument("--web-url", default=None, help="URL to capture the source-of-truth value from (Playwright)")
    parser.add_argument("--web-selector", default=None, help="CSS/Playwright selector for the value on the page")
    parser.add_argument("--web-attribute", default=None, help="Read this attribute instead of inner text (e.g. value)")
    parser.add_argument("--web-headed", action="store_true", help="Run the capture browser headed (visible)")
    parser.add_argument("--prompt", default=None, help="Path to prompt.json (overrides auto-detect)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-step console logging")
    args = parser.parse_args()

    try:
        result = run_goal(
            args.goal,
            args.web_value,
            args.prompt,
            verbose=not args.quiet,
            web_url=args.web_url,
            web_selector=args.web_selector,
            web_attribute=args.web_attribute,
            web_headed=args.web_headed,
        )
    except DeviceError as err:
        raise SystemExit(f"\nDevice error:\n  {err}")
    print(f"\nArtifacts: {result.run_dir}/run.json")


if __name__ == "__main__":
    main()
