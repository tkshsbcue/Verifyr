"""Web-to-mobile parity verifier.

When the agent emits an `assert` action it captures a value it read on the app
screen. This module asks the VLM to judge that value against a source-of-truth
web value, using the verifier prompts from prompt.json. It returns the
{result, confidence, explanation} object.

The web value can be injected directly (CLI flag / goal field) or captured live
from a URL with Playwright via capture_web_value(). resolve_web_value() picks
whichever was provided.
"""

from __future__ import annotations

from .config import PromptConfig
from .vlm import VLMClient, VLMError


def _render(template: str, **values) -> str:
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", str(val))
    return out


def verify(
    vlm: VLMClient,
    prompt: PromptConfig,
    label: str,
    found_value: str,
    web_value: str,
    image_b64: str | None = None,
) -> dict:
    """Run the parity judge. Returns a dict with result/confidence/explanation."""
    user_text = _render(
        prompt.verifier_input_template,
        label=label,
        web_value=web_value,
        app_value=found_value,
    )
    try:
        result = vlm.complete(prompt.verifier_system_prompt, user_text, image_b64)
    except VLMError as err:
        return {
            "result": "inconclusive",
            "confidence": 0.0,
            "explanation": f"verifier model error: {err}",
        }

    # Normalize the shape so downstream code can rely on the keys.
    # rendering_broken is Phase 1 (absent in Phase 0 output -> defaults to False).
    return {
        "result": str(result.get("result", "inconclusive")),
        "rendering_broken": bool(result.get("rendering_broken", False)),
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "explanation": str(result.get("explanation", "")),
    }


class WebCaptureError(RuntimeError):
    pass


def capture_web_value(
    url: str,
    selector: str | None = None,
    *,
    attribute: str | None = None,
    timeout_ms: int = 15000,
    headless: bool = True,
) -> str:
    """Capture the source-of-truth value from a website with Playwright.

    Args:
        url: page to load.
        selector: CSS (or Playwright) selector for the element holding the value.
            If omitted, the page <title> is returned.
        attribute: read this attribute instead of the element's inner text
            (e.g. "value" for an <input>, "content" for a <meta>).
        timeout_ms: navigation / element-wait timeout.
        headless: run the browser headless (set False to watch it).

    Returns the captured text, stripped. Raises WebCaptureError on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:  # pragma: no cover - dependency hint
        raise WebCaptureError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from err

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                # A realistic browser context so retail sites (Amazon, etc.) don't
                # serve a stripped-down or bot-challenge page to a bare headless UA.
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 900},
                    locale="en-US",
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if selector:
                    locator = page.locator(selector).first
                    locator.wait_for(state="visible", timeout=timeout_ms)
                    value = (
                        locator.get_attribute(attribute)
                        if attribute
                        else locator.inner_text()
                    )
                else:
                    value = page.title()
            finally:
                browser.close()
    except WebCaptureError:
        raise
    except Exception as err:
        raise WebCaptureError(f"failed to capture from {url!r}: {err}") from err

    if value is None:
        raise WebCaptureError(
            f"selector {selector!r} matched but yielded no value on {url!r}"
        )
    return value.strip()


def resolve_web_value(
    web_value: str | None = None,
    web_url: str | None = None,
    web_selector: str | None = None,
    web_attribute: str | None = None,
    *,
    headless: bool = True,
) -> str | None:
    """Return the source-of-truth value: explicit value wins, else capture from URL.

    Returns None if neither a value nor a URL was provided (verification is then
    skipped by the agent).
    """
    if web_value is not None:
        return web_value
    if web_url:
        return capture_web_value(
            web_url, web_selector, attribute=web_attribute, headless=headless
        )
    return None
