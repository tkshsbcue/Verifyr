"""Device preflight: make sure an Android emulator/device is available.

Order of operations before connecting Appium:
  1. If a booted device/emulator is already connected (adb), use it.
  2. Otherwise, if an AVD exists on this machine, launch it and wait for boot.
  3. Otherwise, raise a clear DeviceError explaining what to do.

This is local-only (it shells out to `adb` and the `emulator` tool). It is not
meant for a containerized server reaching a remote Appium.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.request
from urllib.parse import urlparse

from .device import DeviceError

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def _run(cmd: list[str], timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""


def booted_devices() -> list[str]:
    """Serials of devices currently in the 'device' (booted) state."""
    out = _run(["adb", "devices"])
    serials = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _find_emulator_binary() -> str | None:
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env)
        if root:
            cand = os.path.join(root, "emulator", "emulator")
            if os.path.isfile(cand):
                return cand
    for cand in (
        os.path.expanduser("~/Library/Android/sdk/emulator/emulator"),  # macOS
        os.path.expanduser("~/Android/Sdk/emulator/emulator"),          # Linux
        os.path.expanduser("~/AppData/Local/Android/Sdk/emulator/emulator.exe"),  # Windows
    ):
        if os.path.isfile(cand):
            return cand
    return shutil.which("emulator")


def list_avds(emulator_bin: str) -> list[str]:
    out = _run([emulator_bin, "-list-avds"])
    return [line.strip() for line in out.splitlines() if line.strip() and " " not in line.strip()]


def _appium_reachable(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/status", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_appium(settings, verbose: bool = True) -> None:
    """Guarantee the Appium server is reachable, starting it locally if needed.

    Uses a running server if reachable. Otherwise, when the URL is local and
    AUTO_START_APPIUM is on, launches `appium` and waits for it. Raises a clear
    DeviceError if Appium isn't installed or a remote server is unreachable.
    """
    def log(msg: str) -> None:
        if verbose:
            print(f"[preflight] {msg}", flush=True)

    url = settings.appium_server_url
    if _appium_reachable(url):
        return

    parsed = urlparse(url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 4723

    if not settings.auto_start_appium:
        raise DeviceError(f"Appium server is not reachable at {url} (AUTO_START_APPIUM is off).")
    if host not in _LOCAL_HOSTS:
        raise DeviceError(
            f"Appium server is not reachable at {url}, and it is remote so Verifyr "
            "cannot start it. Start Appium on that host."
        )

    appium_bin = shutil.which("appium")
    if not appium_bin:
        raise DeviceError(
            "Appium is not reachable and the `appium` CLI is not installed.\n"
            "  Install it once:  npm install -g appium && appium driver install uiautomator2\n"
            "  (requires Node.js), then retry."
        )

    log(f"starting Appium server on {host}:{port}…")
    subprocess.Popen(
        [appium_bin, "--address", "127.0.0.1" if host in _LOCAL_HOSTS else host, "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    timeout = settings.appium_start_timeout
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _appium_reachable(url):
            log("Appium server is up")
            return
        time.sleep(1.5)
    raise DeviceError(
        f"Appium server did not become ready on {host}:{port} within {timeout}s. "
        "Check that the uiautomator2 driver is installed (`appium driver install uiautomator2`)."
    )


def ensure_emulator(settings, verbose: bool = True) -> str:
    """Guarantee a booted device, launching an AVD if needed. Returns its serial.

    Raises DeviceError with an actionable message if nothing is available.
    """
    def log(msg: str) -> None:
        if verbose:
            print(f"[preflight] {msg}", flush=True)

    if not shutil.which("adb"):
        raise DeviceError(
            "`adb` was not found on PATH. Install the Android SDK platform-tools "
            "and ensure `adb` is available."
        )

    # 1. Already have a booted device?
    booted = booted_devices()
    if settings.udid and settings.udid in booted:
        return settings.udid
    if booted:
        log(f"using already-running device: {booted[0]}")
        return booted[0]

    # 2. Find the emulator tool and an AVD to launch.
    emulator_bin = _find_emulator_binary()
    if not emulator_bin:
        raise DeviceError(
            "No running Android emulator/device, and the `emulator` tool was not found.\n"
            "  Install Android Studio (or the SDK) and create an AVD, then either start it\n"
            "  or set ANDROID_HOME so Verifyr can launch it automatically."
        )
    avds = list_avds(emulator_bin)
    if not avds:
        raise DeviceError(
            "No running emulator/device and no AVDs exist on this machine.\n"
            "  Create one in Android Studio → Device Manager (or `avdmanager create avd`),\n"
            "  then retry."
        )

    avd = settings.avd if settings.avd in avds else avds[0]
    if settings.avd and settings.avd not in avds:
        log(f"configured AVD '{settings.avd}' not found; using '{avd}'. Available: {avds}")
    log(f"launching emulator AVD '{avd}'…")

    subprocess.Popen(
        [emulator_bin, "-avd", avd, "-no-snapshot-load"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach so it outlives this process
    )

    timeout = settings.emulator_boot_timeout
    _run(["adb", "wait-for-device"], timeout=timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _run(["adb", "shell", "getprop", "sys.boot_completed"]).strip() == "1":
            serial = booted_devices()
            log(f"emulator '{avd}' booted: {serial[0] if serial else 'ok'}")
            return serial[0] if serial else (settings.udid or "emulator-5554")
        time.sleep(3)
    raise DeviceError(
        f"Emulator '{avd}' did not finish booting within {timeout}s "
        "(raise EMULATOR_BOOT_TIMEOUT if your machine is slow)."
    )
