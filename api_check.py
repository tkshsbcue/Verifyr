"""Cheap backend API check.

Fetches the endpoint the app actually uses and extracts the value at a JSON path.
This is the first, cheapest signal in a parity check — it tells us whether the
change reached the backend at all, before we spend time driving the device.

Uses only the standard library (urllib) so there's no extra dependency.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


def _ssl_context() -> ssl.SSLContext | None:
    """Build an SSL context backed by certifi when available.

    Framework Python on macOS often ships without root certs, which breaks
    HTTPS via urllib. certifi (a transitive dependency) provides them.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


@dataclass
class ApiResult:
    value: str | None
    ok: bool
    error: str | None = None
    raw: Any = None


# Supports dot paths with optional [index]: e.g. data.products[0].price
_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _dig(data: Any, json_path: str) -> Any:
    cur = data
    for key, idx in _TOKEN_RE.findall(json_path):
        if idx != "":
            if not isinstance(cur, list):
                raise KeyError(f"expected list at [{idx}] in path {json_path!r}")
            cur = cur[int(idx)]
        else:
            if not isinstance(cur, dict):
                raise KeyError(f"expected object at {key!r} in path {json_path!r}")
            cur = cur[key]
    return cur


def fetch_api_value(
    endpoint: str,
    json_path: str | None,
    headers: dict | None = None,
    timeout: int = 15,
) -> ApiResult:
    """GET the endpoint and pull the value at json_path. Returns an ApiResult.

    If json_path is None, the whole decoded body is returned as the value.
    Never raises — failures are reported in the result so the orchestrator can
    treat "API unavailable" as a routing signal.
    """
    try:
        req = urllib.request.Request(endpoint, headers=headers or {}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as err:
        return ApiResult(value=None, ok=False, error=f"request failed: {err}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as err:
        return ApiResult(value=None, ok=False, error=f"response was not JSON: {err}", raw=body[:500])

    if not json_path:
        return ApiResult(value=json.dumps(data), ok=True, raw=data)

    try:
        value = _dig(data, json_path)
    except (KeyError, IndexError, TypeError) as err:
        return ApiResult(value=None, ok=False, error=f"json_path {json_path!r} not found: {err}", raw=data)

    # Stringify scalars for comparison; leave structured values JSON-encoded.
    if isinstance(value, (dict, list)):
        value_str = json.dumps(value)
    else:
        value_str = str(value)
    return ApiResult(value=value_str, ok=True, raw=value)
