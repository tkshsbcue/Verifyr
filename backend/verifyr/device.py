"""Appium executor for a single Android device/emulator (uiautomator2).

Responsibilities:
  - connect / launch the app under test
  - capture a screenshot (base64 PNG)
  - capture a compact accessibility tree from the page source
  - one method per action type in the prompt's action_space

Every action method returns an ActionResult so the agent loop can log outcomes
and classify failures without inspecting exceptions.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException

from .config import Settings


# Element-failure marker the agent maps to the "element-not-found" failure bucket.
ERR_ELEMENT_NOT_FOUND = "element-not-found"
ERR_EXECUTOR = "executor-error"


class DeviceError(RuntimeError):
    """Raised for connect/launch problems with an actionable message."""


@dataclass
class ActionResult:
    ok: bool
    detail: str = ""
    error_class: str | None = None
    extra: dict = field(default_factory=dict)


def _center(bounds: str) -> tuple[int, int] | None:
    """Parse Android bounds '[x1,y1][x2,y2]' to a center point."""
    m = re.findall(r"-?\d+", bounds or "")
    if len(m) != 4:
        return None
    x1, y1, x2, y2 = map(int, m)
    return (x1 + x2) // 2, (y1 + y2) // 2


class Device:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver: webdriver.Remote | None = None

    # ---- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        options = UiAutomator2Options().load_capabilities(self.settings.appium_capabilities())
        url = self.settings.appium_server_url
        try:
            self.driver = webdriver.Remote(url, options=options)
        except Exception as err:
            msg = str(err)
            if "Connection refused" in msg or "Max retries" in msg or "NewConnection" in msg:
                raise DeviceError(
                    f"Could not reach the Appium server at {url}.\n"
                    "  Start it in another terminal:  appium\n"
                    "  (install once with:  npm install -g appium && appium driver install uiautomator2)"
                ) from err
            raise DeviceError(
                f"Appium session failed to start: {msg}\n"
                "  Check that an emulator/device is online (`adb devices`) and that "
                "APP_PACKAGE/APP_ACTIVITY or APP_PATH are correct."
            ) from err
        self.driver.implicitly_wait(2)

    def quit(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def window_size(self) -> tuple[int, int]:
        size = self.driver.get_window_size()
        return int(size["width"]), int(size["height"])

    # ---- observation -------------------------------------------------------
    def screenshot_b64(self) -> str:
        return self.driver.get_screenshot_as_base64()

    def accessibility_tree(self, max_elements: int = 80) -> str:
        """Compact, line-per-element view of the visible UI for the prompt."""
        try:
            source = self.driver.page_source
        except WebDriverException as err:
            return f"(failed to read page source: {err})"

        try:
            root = ET.fromstring(source)
        except ET.ParseError:
            return source[:4000]

        lines: list[str] = []
        for el in root.iter():
            attrib = el.attrib
            text = (attrib.get("text") or "").strip()
            desc = (attrib.get("content-desc") or "").strip()
            rid = (attrib.get("resource-id") or "").strip()
            clickable = attrib.get("clickable") == "true"
            scrollable = attrib.get("scrollable") == "true"
            cls = (attrib.get("class") or "").split(".")[-1]

            # Keep only elements a model could plausibly target or read.
            if not (text or desc or rid or clickable or scrollable):
                continue

            parts = [cls or "View"]
            if text:
                parts.append(f'text="{text}"')
            if rid:
                parts.append(f"resource-id={rid}")
            if desc:
                parts.append(f'content-desc="{desc}"')
            flags = []
            if clickable:
                flags.append("clickable")
            if scrollable:
                flags.append("scrollable")
            if flags:
                parts.append("[" + ",".join(flags) + "]")
            center = _center(attrib.get("bounds", ""))
            if center:
                parts.append(f"@({center[0]},{center[1]})")
            lines.append("  ".join(parts))
            if len(lines) >= max_elements:
                lines.append(f"... (truncated at {max_elements} elements)")
                break

        return "\n".join(lines) if lines else "(no interactive elements detected)"

    # ---- element resolution ------------------------------------------------
    def _find(self, target: str):
        """Resolve a target string to an element, trying multiple strategies.

        Accepts optional prefixes (text=, resource-id=, id=, desc=,
        content-desc=, xpath=) or a bare string tried against text/desc/id.
        Returns an element or None.
        """
        if not target:
            return None
        target = target.strip()

        prefix, _, rest = target.partition("=")
        prefix = prefix.strip().lower()
        rest = rest.strip().strip('"').strip("'")

        strategies: list[tuple[str, str]] = []
        if prefix == "text" and rest:
            strategies.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{rest}")'))
            strategies.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{rest}")'))
        elif prefix in ("resource-id", "id") and rest:
            strategies.append((AppiumBy.ID, rest))
            strategies.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().resourceIdMatches(".*{re.escape(rest)}.*")'))
        elif prefix in ("desc", "content-desc", "accessibility-id") and rest:
            strategies.append((AppiumBy.ACCESSIBILITY_ID, rest))
            strategies.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().descriptionContains("{rest}")'))
        elif prefix == "xpath" and rest:
            strategies.append((AppiumBy.XPATH, rest))
        else:
            # Bare value: try text, then content-desc, then resource-id, then xpath.
            value = target
            strategies.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{value}")'))
            strategies.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{value}")'))
            strategies.append((AppiumBy.ACCESSIBILITY_ID, value))
            strategies.append((AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().descriptionContains("{value}")'))
            strategies.append((AppiumBy.XPATH, f'//*[@text="{value}" or @content-desc="{value}"]'))

        for by, sel in strategies:
            try:
                els = self.driver.find_elements(by, sel)
                if els:
                    return els[0]
            except WebDriverException:
                continue
        return None

    @staticmethod
    def _coords_from_text(target: str, w: int, h: int) -> tuple[int, int] | None:
        """Extract explicit coordinates from a location string.

        Supports 'x,y' pixels and 'x%,y%' fractions. Prose locations (e.g.
        'top right corner') are not interpretable deterministically and return
        None — the caller treats that as element-not-found.
        """
        pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%", target or "")
        if len(pcts) >= 2:
            return int(float(pcts[0]) / 100 * w), int(float(pcts[1]) / 100 * h)
        nums = re.findall(r"-?\d+", target or "")
        if len(nums) >= 2:
            return int(nums[0]), int(nums[1])
        return None

    # ---- actions -----------------------------------------------------------
    def tap(self, target: str | None) -> ActionResult:
        if not target:
            return ActionResult(False, "tap requires a target", ERR_EXECUTOR)
        el = self._find(target)
        if el is not None:
            try:
                el.click()
                return ActionResult(True, f"tapped {target}")
            except WebDriverException as err:
                return ActionResult(False, f"click failed: {err}", ERR_EXECUTOR)

        # Fall back to coordinates if the target encodes any.
        w, h = self.window_size()
        coords = self._coords_from_text(target, w, h)
        if coords:
            try:
                self.driver.tap([coords])
                return ActionResult(True, f"tapped coordinates {coords}")
            except WebDriverException as err:
                return ActionResult(False, f"coordinate tap failed: {err}", ERR_EXECUTOR)

        return ActionResult(False, f"could not resolve target: {target}", ERR_ELEMENT_NOT_FOUND)

    def type_text(self, target: str | None, text: str | None) -> ActionResult:
        if text is None:
            return ActionResult(False, "type_text requires text", ERR_EXECUTOR)
        el = self._find(target) if target else None
        try:
            if el is not None:
                try:
                    el.clear()
                except WebDriverException:
                    pass
                el.send_keys(text)
                return ActionResult(True, f"typed into {target}")
            # No field resolved: type into whatever currently has focus.
            self.driver.execute_script("mobile: type", {"text": text})
            return ActionResult(True, "typed into focused field")
        except WebDriverException as err:
            return ActionResult(False, f"type failed: {err}", ERR_ELEMENT_NOT_FOUND)

    def _gesture(self, direction: str, kind: str) -> ActionResult:
        direction = (direction or "down").lower()
        w, h = self.window_size()
        try:
            # uiautomator2 supports scrollGesture/swipeGesture over a screen area.
            gesture = "scrollGesture" if kind == "scroll" else "swipeGesture"
            self.driver.execute_script(
                f"mobile: {gesture}",
                {
                    "left": int(w * 0.1),
                    "top": int(h * 0.2),
                    "width": int(w * 0.8),
                    "height": int(h * 0.6),
                    "direction": direction,
                    "percent": 0.75,
                },
            )
            return ActionResult(True, f"{kind} {direction}")
        except WebDriverException as err:
            return ActionResult(False, f"{kind} failed: {err}", ERR_EXECUTOR)

    def scroll(self, direction: str) -> ActionResult:
        return self._gesture(direction, "scroll")

    def swipe(self, direction: str) -> ActionResult:
        return self._gesture(direction, "swipe")

    def press_back(self) -> ActionResult:
        try:
            self.driver.back()
            return ActionResult(True, "pressed back")
        except WebDriverException as err:
            return ActionResult(False, f"back failed: {err}", ERR_EXECUTOR)

    def relaunch_app(self, package: str | None = None) -> ActionResult:
        """Stale-retry action: terminate and relaunch the app under test."""
        pkg = package or self.settings.app_package
        if not pkg:
            return ActionResult(False, "no package to relaunch", ERR_EXECUTOR)
        try:
            self.driver.terminate_app(pkg)
            self.driver.activate_app(pkg)
            return ActionResult(True, f"relaunched {pkg}")
        except WebDriverException as err:
            return ActionResult(False, f"relaunch failed: {err}", ERR_EXECUTOR)

    def pull_to_refresh(self) -> ActionResult:
        """Stale-retry action: swipe down from near the top to trigger a refresh."""
        w, h = self.window_size()
        try:
            self.driver.execute_script(
                "mobile: swipeGesture",
                {
                    "left": int(w * 0.5) - 5,
                    "top": int(h * 0.25),
                    "width": 10,
                    "height": int(h * 0.5),
                    "direction": "down",
                    "percent": 0.9,
                },
            )
            return ActionResult(True, "pull to refresh")
        except WebDriverException as err:
            return ActionResult(False, f"pull_to_refresh failed: {err}", ERR_EXECUTOR)

    def wait(self, seconds: Any, reason: str = "") -> ActionResult:
        try:
            secs = float(seconds) if seconds is not None else 1.0
        except (TypeError, ValueError):
            secs = 1.0
        secs = max(0.0, min(secs, 5.0))  # clamp to the prompt's 1-5s contract
        time.sleep(secs)
        return ActionResult(True, f"waited {secs}s ({reason})" if reason else f"waited {secs}s")
