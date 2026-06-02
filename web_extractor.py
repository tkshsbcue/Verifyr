"""Web value extraction.

Two paths to the source-of-truth value:
  1. A CSS selector -> use Playwright directly (verifier.capture_web_value).
  2. No selector -> capture the page's visible text (+ screenshot) and ask the
     VLM to extract the value matching a target description.

resolve_web_value_for_check() picks the right path for a Check.
"""

from __future__ import annotations

from config import PromptConfig
from verifier import WebCaptureError, capture_web_value
from vlm import VLMClient, VLMError


def capture_page_text(url: str, timeout_ms: int = 15000, max_chars: int = 6000) -> tuple[str, str | None]:
    """Return (visible_text, screenshot_b64) for a page, using a realistic context."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise WebCaptureError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from err

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            try:
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
                text = page.inner_text("body")
                shot = page.screenshot(full_page=False)
            finally:
                browser.close()
    except WebCaptureError:
        raise
    except Exception as err:
        raise WebCaptureError(f"failed to load {url!r}: {err}") from err

    import base64

    return text[:max_chars], base64.b64encode(shot).decode("ascii")


def _render(template: str, **values) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def extract_web_value(
    vlm: VLMClient,
    prompt: PromptConfig,
    target_description: str,
    url: str,
    page_text: str,
    image_b64: str | None = None,
) -> dict:
    """Ask the VLM to extract one value from page text. Returns {value, found, note}."""
    if not prompt.web_extractor_system_prompt:
        return {"value": "", "found": False, "note": "web_extractor prompt not available (Phase 0 config)"}
    user_text = _render(
        prompt.web_extractor_input_template,
        target_description=target_description,
        url=url,
        page_text=page_text,
    )
    try:
        out = vlm.complete(prompt.web_extractor_system_prompt, user_text, image_b64)
    except VLMError as err:
        return {"value": "", "found": False, "note": f"extractor model error: {err}"}
    return {
        "value": str(out.get("value", "")),
        "found": bool(out.get("found", False)),
        "note": str(out.get("note", "")),
    }


def resolve_web_value_for_check(check, vlm: VLMClient, prompt: PromptConfig) -> tuple[str | None, str]:
    """Resolve the source-of-truth value for a check's web spec.

    Returns (value, note). Prefers the CSS selector; falls back to LLM extraction
    against the page text when no selector is configured.
    """
    web = check.web
    if not web.url:
        return None, "no web.url configured"

    if web.selector:
        try:
            return capture_web_value(web.url, web.selector), f"selector {web.selector!r}"
        except WebCaptureError as err:
            return None, f"selector capture failed: {err}"

    # No selector -> extract from page text with the VLM.
    target = web.target_description or check.app_targets[0].label if check.app_targets else web.target_description
    if not target:
        return None, "no selector and no target_description to extract by"
    try:
        page_text, shot = capture_page_text(web.url)
    except WebCaptureError as err:
        return None, f"page capture failed: {err}"
    result = extract_web_value(vlm, prompt, target, web.url, page_text, shot)
    if result["found"]:
        return result["value"], f"extracted: {result['note']}" if result["note"] else "extracted by VLM"
    return None, f"value not found on page: {result['note']}"
