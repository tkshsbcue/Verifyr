"""Supabase integration: auth-token verification + Storage for run screenshots.

The FastAPI backend stays the gateway between the browser and Supabase:
  - The frontend authenticates directly with Supabase Auth and sends the
    resulting access token as a Bearer token. ``verify_token`` validates it.
  - Run screenshots are uploaded to a private Storage bucket under a per-user
    folder, and served back through an ownership-checked proxy via ``signed_url``.

All Storage calls use the service-role key and therefore bypass RLS; the bucket
policies in the migrations govern any *direct* client access instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .settings import server_settings


@dataclass(frozen=True)
class SupaUser:
    id: str  # auth.users.id (a uuid, kept as a string)
    email: str | None = None


# Short-lived positive cache so we don't introspect Supabase on every request.
_token_cache: dict[str, tuple[SupaUser, float]] = {}
_CACHE_TTL = 30.0  # seconds


def verify_token(token: str | None) -> SupaUser | None:
    """Return the Supabase user for a valid access token, else None."""
    if not token:
        return None
    now = time.time()
    cached = _token_cache.get(token)
    if cached and cached[1] > now:
        return cached[0]

    if not (server_settings.supabase_url and server_settings.supabase_anon_key):
        return None
    try:
        resp = httpx.get(
            f"{server_settings.supabase_url}/auth/v1/user",
            headers={
                "apikey": server_settings.supabase_anon_key,
                "Authorization": f"Bearer {token}",
            },
            timeout=10.0,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    uid = data.get("id")
    if not uid:
        return None
    user = SupaUser(id=str(uid), email=data.get("email"))
    _token_cache[token] = (user, now + _CACHE_TTL)
    return user


def _storage_headers(content_type: str | None = None) -> dict[str, str]:
    key = server_settings.supabase_service_role_key
    headers = {"Authorization": f"Bearer {key}", "apikey": key}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def storage_enabled() -> bool:
    return bool(server_settings.supabase_url and server_settings.supabase_service_role_key)


def upload_artifact(object_path: str, data: bytes, content_type: str = "image/png") -> str | None:
    """Upload bytes to ``<bucket>/<object_path>``. Returns the object path or None.

    Best-effort: on any failure we return None and the caller falls back to the
    locally stored copy.
    """
    if not storage_enabled():
        return None
    bucket = server_settings.supabase_bucket
    try:
        resp = httpx.post(
            f"{server_settings.supabase_url}/storage/v1/object/{bucket}/{object_path}",
            headers={**_storage_headers(content_type), "x-upsert": "true"},
            content=data,
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as err:
        print(f"[storage] upload failed for {object_path}: {err}", flush=True)
        return None
    return object_path


def signed_url(object_path: str, expires_in: int = 3600) -> str | None:
    """Return a time-limited public URL for a stored object, or None."""
    if not storage_enabled():
        return None
    bucket = server_settings.supabase_bucket
    try:
        resp = httpx.post(
            f"{server_settings.supabase_url}/storage/v1/object/sign/{bucket}/{object_path}",
            headers=_storage_headers("application/json"),
            json={"expiresIn": expires_in},
            timeout=10.0,
        )
        resp.raise_for_status()
        body = resp.json()
        signed = body.get("signedURL") or body.get("signedUrl")
        if signed:
            # signedURL is relative to /storage/v1.
            return f"{server_settings.supabase_url}/storage/v1{signed}"
    except httpx.HTTPError as err:
        print(f"[storage] sign failed for {object_path}: {err}", flush=True)
    return None
