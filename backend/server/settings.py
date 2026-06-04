"""Server-side settings, separate from the engine's device/model Settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


@dataclass
class ServerSettings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./verifyr.db"))
    cors_origins: list = field(default_factory=_origins)
    # ---- Supabase (unified auth + Postgres + Storage) ----
    # Auth tokens are verified against SUPABASE_URL; screenshots are stored in
    # the SUPABASE_BUCKET using the service-role key (server-side only).
    supabase_url: str = field(default_factory=lambda: os.environ.get("SUPABASE_URL", "").rstrip("/"))
    supabase_anon_key: str = field(default_factory=lambda: os.environ.get("SUPABASE_ANON_KEY", ""))
    supabase_service_role_key: str = field(
        default_factory=lambda: os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    )
    supabase_bucket: str = field(default_factory=lambda: os.environ.get("SUPABASE_BUCKET", "run-artifacts"))
    # Optional SMTP for alerts; if unset, alerts are logged + stored only.
    smtp_host: str | None = field(default_factory=lambda: os.environ.get("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "587")))
    smtp_user: str | None = field(default_factory=lambda: os.environ.get("SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD"))
    alert_from: str = field(default_factory=lambda: os.environ.get("ALERT_FROM", "verifyr@localhost"))


server_settings = ServerSettings()
