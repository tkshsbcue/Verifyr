"""Reporter hook for streaming structured events out of the engine.

The CLI doesn't need this (it prints to the console), but the server does: it
turns each agent step / parity signal into a live event for WebSocket clients
and a row in the database. The engine stays decoupled — it only knows about the
Reporter interface, not the server.
"""

from __future__ import annotations

from typing import Any, Callable


class Reporter:
    """No-op base. The engine calls emit(); the default does nothing."""

    def emit(self, kind: str, data: dict[str, Any]) -> None:  # noqa: D401
        pass


class CallbackReporter(Reporter):
    """Forwards events to a callable. Exceptions in the callback are swallowed
    so reporting can never crash a run."""

    def __init__(self, callback: Callable[[str, dict[str, Any]], None]):
        self._cb = callback

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        try:
            self._cb(kind, data)
        except Exception:
            pass
