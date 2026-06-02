"""ORM models: User, Check, Run."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    # config holds {web, api, app_targets} — the engine's check schema minus name.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule: Mapped[str | None] = mapped_column(String(120), nullable=True)  # cron expr, e.g. "*/30 * * * *"
    alert_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    runs: Mapped[list["Run"]] = relationship(
        back_populates="check", cascade="all, delete-orphan", order_by="Run.id.desc()"
    )


class Apk(Base):
    __tablename__ = "apks"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512))
    package: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Either a saved check (parity) or an ad-hoc apk+goal (quick test).
    check_id: Mapped[int | None] = mapped_column(
        ForeignKey("checks.id", ondelete="CASCADE"), index=True, nullable=True
    )
    apk_id: Mapped[int | None] = mapped_column(ForeignKey("apks.id"), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # ad-hoc inputs (web source-of-truth)
    # queued | running | done | error  (verdict carries the parity classification)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    verdict: Mapped[str | None] = mapped_column(String(48), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    signals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    out_dir: Mapped[str | None] = mapped_column(String(512), nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")  # manual | scheduled
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    check: Mapped["Check"] = relationship(back_populates="runs")
