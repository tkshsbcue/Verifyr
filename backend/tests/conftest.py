"""Shared test fixtures.

The server reads DATABASE_URL / SUPABASE_* at import time, so we point the DB at
a throwaway SQLite file *before* importing any server module. Auth is replaced
with a dependency override (no real Supabase calls), and the background worker is
stubbed so triggering a run never touches a device.
"""

from __future__ import annotations

import os
import tempfile
from concurrent.futures import Future

# Must be set before `server.*` is imported (settings/db read env at import time).
_TMP = tempfile.mkdtemp(prefix="verifyr-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import deps, runner  # noqa: E402
from server.db import Base, SessionLocal, engine  # noqa: E402
from server.main import app  # noqa: E402
from server.supabase_client import SupaUser  # noqa: E402

USER_A = SupaUser(id="11111111-1111-1111-1111-111111111111", email="a@example.com")
USER_B = SupaUser(id="22222222-2222-2222-2222-222222222222", email="b@example.com")


@pytest.fixture(autouse=True)
def fresh_db():
    """A clean schema for every test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def stub_worker(monkeypatch):
    """Stop the thread pool from actually executing runs.

    Returns an un-run Future, which is what the cancellation registry expects for
    a still-queued run (``future.cancel()`` succeeds), so cancel tests work.
    """
    monkeypatch.setattr(runner._executor, "submit", lambda fn, *a, **k: Future())
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def as_user():
    """Set the authenticated user for API requests; defaults to USER_A."""

    def _set(user: SupaUser = USER_A):
        app.dependency_overrides[deps.current_user] = lambda: user

    _set(USER_A)
    yield _set
    app.dependency_overrides.pop(deps.current_user, None)


@pytest.fixture
def client(as_user):
    # No context manager -> the app's startup/shutdown (scheduler, static mount)
    # don't fire; we manage the schema ourselves.
    return TestClient(app)
