"""Installed app build detection and version comparison.

Used for the needs_app_release classification: if a value requires a newer app
build than what's installed, it cannot appear no matter what the backend returns.

Reads the installed versionName via adb (dumpsys package). Falls back gracefully
if adb or the package isn't available.
"""

from __future__ import annotations

import re
import subprocess


def installed_build(package: str, udid: str | None = None) -> str | None:
    """Return the installed app's versionName, or None if it can't be read."""
    if not package:
        return None
    cmd = ["adb"]
    if udid:
        cmd += ["-s", udid]
    cmd += ["shell", "dumpsys", "package", package]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    m = re.search(r"versionName=(\S+)", out)
    return m.group(1) if m else None


def _parts(version: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", version or "")]


def version_lt(installed: str | None, required: str | None) -> bool:
    """True if `installed` is strictly older than `required`.

    Compares numeric components left-to-right (semver-ish). If either side is
    missing or unparseable, returns False (we don't claim "older" without proof).
    """
    if not installed or not required:
        return False
    a, b = _parts(installed), _parts(required)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return a < b
