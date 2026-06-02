"""In-process event bus bridging the (threaded) runner to async WebSocket clients.

The runner executes the blocking engine in a worker thread and pushes events via
publish_threadsafe(); WebSocket handlers subscribe() to a run's asyncio.Queue.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, run_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: int, q: asyncio.Queue) -> None:
        self._subs[run_id].discard(q)
        if not self._subs[run_id]:
            self._subs.pop(run_id, None)

    def publish_threadsafe(self, run_id: int, event: dict[str, Any]) -> None:
        """Called from the runner thread."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._publish, run_id, event)

    def _publish(self, run_id: int, event: dict[str, Any]) -> None:
        for q in list(self._subs.get(run_id, ())):
            q.put_nowait(event)


bus = EventBus()
