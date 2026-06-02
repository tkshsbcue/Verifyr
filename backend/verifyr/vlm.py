"""Provider-agnostic vision-language-model client.

A VLM call here is always: system prompt + a single user text + one screenshot
image, returning a parsed JSON object. The model is expected to reply with a bare
JSON object, but we defensively strip code fences and retry once on malformed
output before giving up.

Two providers are implemented: OpenAI (default) and Anthropic. Add another by
subclassing VLMClient and registering it in get_vlm().
"""

from __future__ import annotations

import json
import re
from typing import Any

from .config import Settings


class VLMError(RuntimeError):
    pass


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from a model reply.

    Handles ```json fences and leading/trailing prose by slicing to the outer
    braces. Raises ValueError if nothing parseable is found.
    """
    if text is None:
        raise ValueError("empty model response")
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost { ... } span.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError(f"no JSON object found in model response: {text[:200]!r}")


class VLMClient:
    """Base class. Subclasses implement _raw_complete()."""

    def __init__(self, model: str, temperature: float, max_tokens: int):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _raw_complete(self, system: str, user_text: str, image_b64: str | None) -> str:
        raise NotImplementedError

    def complete(self, system: str, user_text: str, image_b64: str | None = None) -> dict:
        """Send a turn and return parsed JSON. Retries once on malformed JSON."""
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                raw = self._raw_complete(system, user_text, image_b64)
                return extract_json(raw)
            except ValueError as err:
                last_err = err
                # Nudge the model to emit clean JSON on the retry.
                user_text = (
                    user_text
                    + "\n\nIMPORTANT: Your previous reply was not valid JSON. "
                    "Respond with ONLY a single JSON object, no prose, no code fences."
                )
        raise VLMError(f"model did not return valid JSON after retry: {last_err}")


class OpenAIVLM(VLMClient):
    def __init__(self, model, temperature, max_tokens, api_key):
        super().__init__(model, temperature, max_tokens)
        from openai import OpenAI  # imported lazily so the dep is optional per-provider

        self._client = OpenAI(api_key=api_key)
        # Newer models (gpt-5.x, o-series) use max_completion_tokens and may reject
        # a custom temperature. We adapt on first use and remember the choice.
        self._token_param = "max_tokens"
        self._send_temperature = True

    def _build_kwargs(self, system: str, content: list, messages_only: bool = False) -> dict:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            self._token_param: self.max_tokens,
        }
        if self._send_temperature:
            kwargs["temperature"] = self.temperature
        return kwargs

    def _raw_complete(self, system: str, user_text: str, image_b64: str | None) -> str:
        from openai import BadRequestError

        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        if image_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                }
            )

        # Up to two adaptations: token-param rename and temperature removal.
        for _ in range(3):
            try:
                resp = self._client.chat.completions.create(**self._build_kwargs(system, content))
                return resp.choices[0].message.content or ""
            except BadRequestError as err:
                msg = str(err)
                if "max_tokens" in msg and "max_completion_tokens" in msg and self._token_param == "max_tokens":
                    self._token_param = "max_completion_tokens"
                    continue
                if "temperature" in msg and self._send_temperature:
                    self._send_temperature = False
                    continue
                raise
        # Final attempt surfaces the real error.
        resp = self._client.chat.completions.create(**self._build_kwargs(system, content))
        return resp.choices[0].message.content or ""


class AnthropicVLM(VLMClient):
    def __init__(self, model, temperature, max_tokens, api_key):
        super().__init__(model, temperature, max_tokens)
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)

    def _raw_complete(self, system: str, user_text: str, image_b64: str | None) -> str:
        content: list[dict[str, Any]] = []
        if image_b64:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_b64,
                    },
                }
            )
        content.append({"type": "text", "text": user_text})
        resp = self._client.messages.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        # Concatenate any text blocks in the reply.
        return "".join(block.text for block in resp.content if block.type == "text")


def get_vlm(settings: Settings) -> VLMClient:
    provider = settings.vlm_provider
    if provider == "openai":
        if not settings.openai_api_key:
            raise VLMError("OPENAI_API_KEY is not set.")
        return OpenAIVLM(
            settings.vlm_model, settings.temperature, settings.max_tokens, settings.openai_api_key
        )
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise VLMError("ANTHROPIC_API_KEY is not set.")
        return AnthropicVLM(
            settings.vlm_model, settings.temperature, settings.max_tokens, settings.anthropic_api_key
        )
    raise VLMError(f"unknown VLM_PROVIDER: {provider!r} (expected 'openai' or 'anthropic')")
