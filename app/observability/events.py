"""Slice 8: the live event bus behind the dashboard's WebSocket.

Bridges synchronous publishers (app/matching/matcher.py, app/services/
ride_service.py -- running on Starlette's sync-route worker threads) to
asynchronous WebSocket consumers (this project's first async code), using
`asyncio.Queue` + `loop.call_soon_threadsafe` -- the standard cross-thread
hand-off pattern.

An earlier draft bridged these with a stdlib `queue.Queue` drained by
offloading its blocking `get()` to a worker thread from async code
(`await anyio.to_thread.run_sync(queue.get)`). That has a real bug: a plain
`Queue.get()` can't be cancelled from outside, so cancelling the consumer
task on shutdown either hangs waiting for an event that may never come, or
leaks a permanently-blocked thread. `tests/conftest.py`'s `client` fixture
runs ASGI lifespan on every HTTP-level test, so that bug would leak one
thread per HTTP test. `asyncio.Queue.get()` is a real coroutine `await` and
is natively cancellable, so `run_broadcast_loop` below shuts down cleanly.

The bus is attached/detached per app lifespan (see app/main.py), not created
once at import time: `publish()` is a no-op when nothing is attached, so the
majority of this project's tests -- which call create_ride/match_ride/
cancel_ride/complete_ride directly against a bare db_session, never starting
a lifespan -- do zero work instead of filling a queue nobody drains.
Recreating the queue per lifespan also avoids one test's direct-call events
sitting around and later being broadcast to a different, unrelated test's
WebSocket connection.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("app.observability.events")


@dataclass
class Event:
    type: str
    timestamp: datetime
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "timestamp": self.timestamp.isoformat(), "data": self.data}


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[Event] | None = None
        self._connections: set[WebSocket] = set()

    def attach(self, loop: asyncio.AbstractEventLoop, *, maxsize: int) -> None:
        self._loop = loop
        self._queue = asyncio.Queue(maxsize=maxsize)

    def detach(self) -> None:
        self._loop = None
        self._queue = None
        self._connections.clear()

    def publish(self, event: Event) -> None:
        """Thread-safe: safe to call from any thread, including Starlette's
        sync-route worker threads. A no-op if no lifespan is currently
        attached (see module docstring)."""
        loop, queue = self._loop, self._queue
        if loop is None or queue is None:
            return
        loop.call_soon_threadsafe(self._put_nowait, queue, event)

    @staticmethod
    def _put_nowait(queue: asyncio.Queue[Event], event: Event) -> None:
        # Runs on the event loop thread (via call_soon_threadsafe), so this
        # itself needs no additional locking.
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("dashboard event queue full, dropping event: %s", event.type)

    async def register(self, websocket: WebSocket) -> None:
        self._connections.add(websocket)

    async def unregister(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def run_broadcast_loop(self) -> None:
        """Started once per lifespan (see app/main.py). Consumes attach()'s
        queue and fans each event out to every currently-connected
        WebSocket. A dead/broken connection is dropped from the registry
        rather than raising -- one slow or gone client must never stop the
        broadcast loop for everyone else."""
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            payload = event.to_json()
            for websocket in list(self._connections):
                try:
                    await websocket.send_json(payload)
                except Exception:
                    self._connections.discard(websocket)


event_bus = EventBus()


def emit(event_type: str, message: str, **data: Any) -> None:
    """Logs at INFO and publishes the same event in one call, so a log line
    and its dashboard event can't silently drift apart over time. Used from
    business logic (app/matching/matcher.py, app/services/ride_service.py),
    not from the API layer directly."""
    logger.info(message)
    event_bus.publish(Event(type=event_type, timestamp=datetime.now(UTC), data=data))
