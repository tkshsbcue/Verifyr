"""Deterministic login automation (runs before the agent's goal).

The Empire app's intended automated-entry path is the "DEV · impersonate user"
flow: open it, paste the Supabase service-role key, load users, pick one. We do
this as scripted steps rather than via the LLM so that:
  - the secret key never goes into a prompt,
  - typing a long JWT is reliable, and
  - the agent's step budget is spent on the actual goal, not on logging in.

The key is read from settings (env IMPERSONATE_KEY), never logged.
"""

from __future__ import annotations

import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException

from config import Settings
from device import ActionResult, Device


def _find_edit_field(device: Device):
    els = device.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
    return els[0] if els else None


def _already_logged_in(device: Device) -> bool:
    """Heuristic: we're past auth if neither the login CTA nor the key field is present."""
    tree = device.accessibility_tree()
    return "DEV · impersonate user" not in tree and "Welcome Back" not in tree


def dev_impersonate_login(device: Device, settings: Settings, verbose: bool = True) -> ActionResult:
    """Run the dev-impersonate login. Returns ActionResult(ok=...) with detail."""
    def log(msg: str) -> None:
        if verbose:
            print(f"[login] {msg}", flush=True)

    if not settings.impersonate_key:
        return ActionResult(False, "IMPERSONATE_KEY is not set", "executor-error")

    device.wait(2, "settle after launch")
    if _already_logged_in(device):
        log("already past login; skipping")
        return ActionResult(True, "already logged in")

    # 1. Open the impersonate screen.
    r = device.tap("DEV · impersonate user")
    if not r.ok:
        return ActionResult(False, f"could not open impersonate screen: {r.detail}", r.error_class)
    device.wait(2, "impersonate screen")

    # 2. Paste the service-role key into the field (never logged).
    field = _find_edit_field(device)
    if field is None:
        return ActionResult(False, "service-role key field not found", "element-not-found")
    try:
        field.clear()
        field.send_keys(settings.impersonate_key)
    except WebDriverException as err:
        return ActionResult(False, f"failed to enter key: {err}", "executor-error")
    log("service-role key entered")
    device.wait(1, "after key entry")

    # 3. Load users.
    r = device.tap("Save & load users")
    if not r.ok:
        return ActionResult(False, f"could not tap Save & load users: {r.detail}", r.error_class)
    device.wait(4, "loading users")

    # If the app complained about a missing/invalid key, surface it.
    tree = device.accessibility_tree()
    if "Paste the service-role key first" in tree or "No key" in tree:
        device.tap("OK")
        return ActionResult(False, "app rejected the key (No key)", "executor-error")
    if "Invalid" in tree and "key" in tree.lower():
        device.tap("OK")
        return ActionResult(False, "app reported an invalid key", "executor-error")

    # 4. Pick a user.
    r = _select_user(device, settings.impersonate_user, verbose)
    if not r.ok:
        return r
    device.wait(3, "entering app")
    log("login complete")
    return ActionResult(True, "dev-impersonate login complete")


def _select_user(device: Device, user: str | None, verbose: bool) -> ActionResult:
    """Pick a user from the loaded list.

    If `user` is given, tap a matching row; otherwise tap the first selectable
    user row. The exact list UI is discovered at runtime from the tree.
    """
    def log(msg: str) -> None:
        if verbose:
            print(f"[login] {msg}", flush=True)

    if user:
        r = device.tap(user)
        if r.ok:
            log(f"selected user matching {user!r}")
            # Some flows need a confirm tap after selecting.
            for confirm in ("Impersonate", "Continue", "Login", "Select"):
                if confirm in device.accessibility_tree():
                    device.tap(confirm)
                    break
            return r
        return ActionResult(False, f"could not find user {user!r}: {r.detail}", r.error_class)

    # No specific user: tap the first clickable row that looks like a user entry.
    try:
        candidates = device.driver.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().clickable(true).className("android.view.ViewGroup")',
        )
    except WebDriverException:
        candidates = []
    # Skip the navigate-up / structural controls by preferring rows below the header.
    for el in candidates:
        try:
            desc = (el.get_attribute("content-desc") or "").strip()
        except WebDriverException:
            desc = ""
        if desc and desc not in ("Navigate up", "Save & load users"):
            el.click()
            log(f"selected first user row: {desc!r}")
            return ActionResult(True, f"selected user {desc!r}")
    return ActionResult(False, "no user row found after loading users", "element-not-found")


def perform_login(device: Device, settings: Settings, verbose: bool = True) -> ActionResult:
    """Dispatch to the configured login flow. No-op if LOGIN_FLOW is unset."""
    if not settings.login_flow:
        return ActionResult(True, "no login flow configured")
    if settings.login_flow == "dev_impersonate":
        return dev_impersonate_login(device, settings, verbose)
    return ActionResult(False, f"unknown LOGIN_FLOW: {settings.login_flow!r}", "executor-error")
