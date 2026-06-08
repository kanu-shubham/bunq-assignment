"""AG-UI SSE streaming endpoint."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.services.sse import subscribe, unsubscribe, sse_format
from app.models.domain import AGUIEventType
from app.services.sse import emit

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def event_stream(workflow_id: str | None = None):
    """SSE endpoint — streams AG-UI events for a workflow or all events."""
    queue = subscribe(workflow_id)

    async def generator():
        # Send initial connected event
        yield sse_format({"event_type": "CONNECTED", "workflow_id": workflow_id})
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield sse_format(data)
                except asyncio.TimeoutError:
                    # Heartbeat
                    yield ": heartbeat\n\n"
        finally:
            unsubscribe(queue, workflow_id)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
