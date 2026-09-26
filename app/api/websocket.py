"""Slice 8: the dashboard's live event stream.

The project's first async def code -- Starlette's WebSocket support requires
it. Purely a server-to-client append-only stream once connected (see
app/observability/events.py): the dashboard polls GET /drivers and GET
/stats for current state, and uses this connection only for the live event
log and transient map animations. `receive_text()` is called in a loop
purely to detect disconnect -- the dashboard never sends anything over this
connection today.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.observability.events import event_bus

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    await event_bus.register(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await event_bus.unregister(websocket)
