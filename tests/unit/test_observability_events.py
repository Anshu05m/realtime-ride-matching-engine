"""Slice 8: pure/asyncio unit tests for the event bus. No DB/network needed.

The WebSocket send/broadcast path itself is exercised end-to-end by
tests/integration/test_websocket.py -- these tests focus on the two things
that are easy to get subtly wrong in isolation: publish() being a genuine
no-op before attach()/after detach(), and attach() giving each lifespan a
fresh queue.
"""

import asyncio

import pytest

from app.observability.events import Event, EventBus


def _make_event(event_type: str = "TEST_EVENT") -> Event:
    import datetime

    return Event(type=event_type, timestamp=datetime.datetime.now(datetime.UTC), data={"x": 1})


def test_publish_before_attach_is_a_silent_no_op():
    bus = EventBus()
    bus.publish(_make_event())  # must not raise, must not block


@pytest.mark.asyncio
async def test_publish_after_detach_is_a_silent_no_op():
    bus = EventBus()
    bus.attach(asyncio.get_running_loop(), maxsize=10)
    bus.detach()
    bus.publish(_make_event())  # must not raise


@pytest.mark.asyncio
async def test_attach_gives_each_lifespan_a_fresh_queue():
    bus = EventBus()
    bus.attach(asyncio.get_running_loop(), maxsize=10)
    bus.publish(_make_event("FIRST"))
    await asyncio.sleep(0)  # let call_soon_threadsafe's callback run
    first_queue = bus._queue
    assert first_queue.qsize() == 1

    bus.detach()
    bus.attach(asyncio.get_running_loop(), maxsize=10)

    # The second lifespan's queue must be empty -- not carrying over the
    # first lifespan's unconsumed event.
    assert bus._queue is not first_queue
    assert bus._queue.qsize() == 0


@pytest.mark.asyncio
async def test_publish_delivers_to_the_queue_for_the_broadcast_loop_to_consume():
    bus = EventBus()
    bus.attach(asyncio.get_running_loop(), maxsize=10)
    event = _make_event("DRIVER_ASSIGNED")

    bus.publish(event)
    delivered = await asyncio.wait_for(bus._queue.get(), timeout=1)

    assert delivered is event


@pytest.mark.asyncio
async def test_publish_drops_events_when_the_queue_is_full_instead_of_blocking():
    bus = EventBus()
    bus.attach(asyncio.get_running_loop(), maxsize=1)

    bus.publish(_make_event("FIRST"))
    bus.publish(_make_event("SECOND"))  # queue is full -- must be dropped, not block
    await asyncio.sleep(0)

    assert bus._queue.qsize() == 1
    remaining = bus._queue.get_nowait()
    assert remaining.type == "FIRST"


@pytest.mark.asyncio
async def test_register_and_unregister_track_connections():
    bus = EventBus()

    class FakeWebSocket:
        pass

    ws = FakeWebSocket()
    await bus.register(ws)
    assert ws in bus._connections

    await bus.unregister(ws)
    assert ws not in bus._connections
