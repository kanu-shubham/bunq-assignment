"""
AG-UI SSE event bus — in-memory broadcast for local dev.
Real production would use Redis pub/sub.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime
from typing import Any
from app.models.domain import AGUIEvent, AGUIEventType


# Global subscriber registry: workflow_id → set of queues
# None key = subscribe to all events
_subscribers: dict[str | None, list[asyncio.Queue]] = {}


def subscribe(workflow_id: str | None = None) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    key = workflow_id
    if key not in _subscribers:
        _subscribers[key] = []
    _subscribers[key].append(queue)
    return queue


def unsubscribe(queue: asyncio.Queue, workflow_id: str | None = None):
    key = workflow_id
    if key in _subscribers:
        try:
            _subscribers[key].remove(queue)
        except ValueError:
            pass


async def publish(event: AGUIEvent):
    """Publish event to all relevant subscribers."""
    data = event.model_dump(mode="json")
    # Send to workflow-specific subscribers
    for key in (event.workflow_id, None):
        for q in list(_subscribers.get(key, [])):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass  # Drop if subscriber is slow


async def emit(
    event_type: AGUIEventType,
    workflow_id: str | None = None,
    payload: dict[str, Any] | None = None,
):
    event = AGUIEvent(
        event_type=event_type,
        workflow_id=workflow_id,
        payload=payload or {},
    )
    await publish(event)


def sse_format(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
