"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ---- auth ----
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class UserOut(BaseModel):
    id: int
    email: EmailStr
    is_active: bool

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- check config ----
class WebSpec(BaseModel):
    url: str = ""
    selector: str | None = None
    target_description: str | None = None


class ApiSpec(BaseModel):
    endpoint: str | None = None
    json_path: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class AppTarget(BaseModel):
    platform: str = "android"
    package: str | None = None
    goal: str = ""
    label: str = ""
    requires_build: str | None = None


class CheckConfig(BaseModel):
    web: WebSpec = Field(default_factory=WebSpec)
    api: ApiSpec = Field(default_factory=ApiSpec)
    app_targets: list[AppTarget] = Field(default_factory=list)


class CheckCreate(BaseModel):
    name: str
    config: CheckConfig = Field(default_factory=CheckConfig)
    schedule: str | None = None
    alert_email: str | None = None
    enabled: bool = True


class CheckUpdate(BaseModel):
    name: str | None = None
    config: CheckConfig | None = None
    schedule: str | None = None
    alert_email: str | None = None
    enabled: bool | None = None


class CheckOut(BaseModel):
    id: int
    name: str
    config: dict[str, Any]
    schedule: str | None
    alert_email: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    last_verdict: str | None = None

    class Config:
        from_attributes = True


# ---- apks ----
class ApkOut(BaseModel):
    id: int
    filename: str
    package: str | None
    version: str | None
    label: str | None
    uploaded_at: datetime

    class Config:
        from_attributes = True


# ---- quick (ad-hoc) run ----
class QuickRunCreate(BaseModel):
    apk_id: int
    goal: str
    name: str | None = None
    web_value: str | None = None
    web_url: str | None = None
    web_selector: str | None = None
    web_attribute: str | None = None


# ---- runs ----
class RunSummary(BaseModel):
    id: int
    check_id: int | None
    apk_id: int | None = None
    name: str | None = None
    goal: str | None = None
    status: str
    verdict: str | None
    confidence: float | None
    trigger: str
    started_at: datetime
    finished_at: datetime | None

    class Config:
        from_attributes = True


class RunOut(RunSummary):
    summary: str | None
    recommended_action: str | None
    signals: dict[str, Any] | None
    detail: dict[str, Any] | None
    steps: list[Any]
    error: str | None
    out_dir: str | None
