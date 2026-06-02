"""The propagation classifier — the core of Phase 1.

Given the signals from one parity check (web value, API value, app UI value,
verifier result, rendering state, build versions), it explains WHY the check
passed or failed and what to do next. Verdicts:

  pass | stale_client_cache | not_propagated_to_backend |
  needs_app_release | rendering_issue | inconclusive
"""

from __future__ import annotations

from .config import PromptConfig
from .vlm import VLMClient, VLMError

VALID_VERDICTS = {
    "pass",
    "stale_client_cache",
    "not_propagated_to_backend",
    "needs_app_release",
    "rendering_issue",
    "inconclusive",
}


def _render(template: str, **values) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", "null" if v is None else str(v))
    return out


def classify(vlm: VLMClient, prompt: PromptConfig, signals: dict) -> dict:
    """Run the classifier over a signals dict.

    signals keys: check_name, label, web_value, api_value, app_ui_value,
    verifier_result, rendering_broken, installed_build, requires_build,
    stale_retry_done.
    """
    if not prompt.classifier_system_prompt:
        return {
            "verdict": "inconclusive",
            "confidence": 0.0,
            "summary": "Classifier prompt unavailable (Phase 0 config loaded).",
            "recommended_action": "Run with the Phase 1 prompt (prompt1.json).",
        }

    user_text = _render(
        prompt.classifier_input_template,
        check_name=signals.get("check_name", ""),
        label=signals.get("label", ""),
        web_value=signals.get("web_value"),
        api_value=signals.get("api_value"),
        app_ui_value=signals.get("app_ui_value"),
        verifier_result=signals.get("verifier_result"),
        rendering_broken=signals.get("rendering_broken"),
        installed_build=signals.get("installed_build"),
        requires_build=signals.get("requires_build"),
        stale_retry_done=signals.get("stale_retry_done"),
    )
    try:
        out = vlm.complete(prompt.classifier_system_prompt, user_text)
    except VLMError as err:
        return {
            "verdict": "inconclusive",
            "confidence": 0.0,
            "summary": f"Classifier model error: {err}",
            "recommended_action": "Retry the check.",
        }

    verdict = str(out.get("verdict", "inconclusive"))
    if verdict not in VALID_VERDICTS:
        verdict = "inconclusive"
    return {
        "verdict": verdict,
        "confidence": float(out.get("confidence", 0.0) or 0.0),
        "summary": str(out.get("summary", "")),
        "recommended_action": str(out.get("recommended_action", "")),
    }
