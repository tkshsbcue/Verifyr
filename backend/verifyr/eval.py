"""Eval harness: run a set of goals N times and summarize reliability.

Input: a goals file (JSON or YAML). Each goal is either a bare string or an
object: {"goal": "...", "web_value": "..."} (web_value optional).

Output: a per-goal summary table with Pass@1, Pass@N, average step count,
average latency, plus a failure tally across these buckets:
  wrong-tap / loop / element-not-found / verifier-mismatch

Usage:
  python eval.py --goals goals.json --runs 3
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import Counter

from .agent import (
    FAIL_ELEMENT_NOT_FOUND,
    FAIL_LOOP,
    FAIL_VERIFIER_MISMATCH,
    FAIL_WRONG_TAP,
    STATUS_PASS,
    Agent,
)
from .config import load_all
from .device import Device
from .verifier import WebCaptureError, resolve_web_value
from .vlm import get_vlm

FAILURE_BUCKETS = (FAIL_WRONG_TAP, FAIL_LOOP, FAIL_ELEMENT_NOT_FOUND, FAIL_VERIFIER_MISMATCH)


def load_goals(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        if path.endswith((".yaml", ".yml")):
            import yaml

            data = yaml.safe_load(fh)
        else:
            data = json.load(fh)

    if isinstance(data, dict) and "goals" in data:
        data = data["goals"]
    if not isinstance(data, list):
        raise ValueError("goals file must be a list (or an object with a 'goals' list)")

    goals: list[dict] = []
    for item in data:
        if isinstance(item, str):
            goals.append({"goal": item, "web_value": None})
        elif isinstance(item, dict):
            goals.append(
                {
                    "goal": item["goal"],
                    "web_value": item.get("web_value"),
                    "web_url": item.get("web_url"),
                    "web_selector": item.get("web_selector"),
                    "web_attribute": item.get("web_attribute"),
                }
            )
        else:
            raise ValueError(f"invalid goal entry: {item!r}")
    return goals


def _fmt(value: float) -> str:
    return f"{value:.1f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifyr eval harness")
    parser.add_argument("--goals", default="goals.json", help="Path to goals JSON/YAML")
    parser.add_argument("--runs", type=int, default=3, help="Runs per goal (N)")
    parser.add_argument("--prompt", default=None, help="Path to prompt.json")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-step logs")
    args = parser.parse_args()

    prompt, settings = load_all(args.prompt)
    issues = settings.validate_for_run()
    if issues:
        raise SystemExit("Cannot run eval:\n  - " + "\n  - ".join(issues))

    goals = load_goals(args.goals)
    vlm = get_vlm(settings)

    eval_root = os.path.join(settings.runs_dir, "eval-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(eval_root, exist_ok=True)

    summary_rows: list[dict] = []
    total_tally: Counter = Counter()

    for gi, g in enumerate(goals):
        goal = g["goal"]
        # Resolve the source-of-truth value once per goal (capture URL only once).
        try:
            web_value = resolve_web_value(
                g.get("web_value"), g.get("web_url"), g.get("web_selector"), g.get("web_attribute")
            )
        except WebCaptureError as err:
            print(f"web capture failed for goal {gi + 1}: {err}")
            web_value = None
        statuses: list[str] = []
        steps: list[int] = []
        latencies: list[float] = []
        tally: Counter = Counter()

        print(f"\n########## GOAL {gi + 1}/{len(goals)}: {goal} ##########")
        if web_value is not None:
            print(f"source-of-truth value: {web_value!r}")
        for run_i in range(args.runs):
            print(f"\n----- run {run_i + 1}/{args.runs} -----")
            device = Device(settings)
            device.connect()
            try:
                agent = Agent(prompt, settings, vlm, device, verbose=not args.quiet)
                run_dir = os.path.join(eval_root, f"goal{gi + 1}-run{run_i + 1}")
                result = agent.run(goal, web_value, run_root=run_dir)
            except Exception as err:  # never let one run kill the whole eval
                print(f"run errored: {err}")
                statuses.append("error")
                tally[FAIL_WRONG_TAP] += 1
                continue
            finally:
                device.quit()

            statuses.append(result.status)
            steps.append(result.steps_taken)
            latencies.append(result.latency_seconds)
            if result.status != STATUS_PASS and result.failure_reason:
                tally[result.failure_reason] += 1

        passes = [s == STATUS_PASS for s in statuses]
        pass_at_1 = 1.0 if (passes and passes[0]) else 0.0
        pass_at_n = 1.0 if any(passes) else 0.0
        total_tally.update(tally)

        summary_rows.append(
            {
                "goal": goal,
                "runs": len(statuses),
                "pass_at_1": pass_at_1,
                "pass_at_n": pass_at_n,
                "pass_rate": (sum(passes) / len(passes)) if passes else 0.0,
                "avg_steps": (sum(steps) / len(steps)) if steps else 0.0,
                "avg_latency": (sum(latencies) / len(latencies)) if latencies else 0.0,
                "failures": dict(tally),
            }
        )

    n = args.runs
    print("\n\n================= EVAL SUMMARY =================")
    header = f"{'Goal':<40} {'Pass@1':>7} {f'Pass@{n}':>7} {'AvgSteps':>9} {'AvgLat(s)':>10}"
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        goal_label = (row["goal"][:37] + "...") if len(row["goal"]) > 40 else row["goal"]
        print(
            f"{goal_label:<40} {row['pass_at_1']:>7.0%} {row['pass_at_n']:>7.0%} "
            f"{_fmt(row['avg_steps']):>9} {_fmt(row['avg_latency']):>10}"
        )

    print("\nFailure tally (all goals x runs):")
    if total_tally:
        for bucket in FAILURE_BUCKETS:
            if total_tally.get(bucket):
                print(f"  {bucket:<22} {total_tally[bucket]}")
    else:
        print("  (none)")

    out_path = os.path.join(eval_root, "summary.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"runs_per_goal": n, "summary": summary_rows, "failure_tally": dict(total_tally)},
            fh,
            indent=2,
        )
    print(f"\nFull summary written to {out_path}")


if __name__ == "__main__":
    main()
