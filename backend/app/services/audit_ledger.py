"""Append-only hash-chained audit ledger."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime
from typing import Optional, Any
import aiosqlite
from app.models.domain import AuditEvent, new_id
from app.services.database import get_db


def compute_hash(parent_hash: str, event_type: str, payload: dict, timestamp: str, actor: str) -> str:
    """SHA-256 hash of concatenated fields for tamper detection."""
    data = f"{parent_hash}|{event_type}|{json.dumps(payload, sort_keys=True)}|{timestamp}|{actor}"
    return hashlib.sha256(data.encode()).hexdigest()


async def get_latest_hash(db: aiosqlite.Connection) -> str:
    """Return the hash of the most recent audit event, or 'GENESIS'."""
    row = await db.execute_fetchall(
        "SELECT hash FROM audit_events ORDER BY timestamp DESC, id DESC LIMIT 1"
    )
    if row:
        return row[0]["hash"]
    return "GENESIS"


async def append_event(
    event_type: str,
    payload: dict[str, Any],
    actor: str = "system",
    workflow_id: Optional[str] = None,
    llm_input: Optional[str] = None,
    llm_output: Optional[str] = None,
) -> AuditEvent:
    """Append an event to the audit ledger and return it with computed hash."""
    db = await get_db()
    try:
        # Acquire latest hash (serialized within transaction)
        await db.execute("BEGIN IMMEDIATE")
        parent_hash = await get_latest_hash(db)
        timestamp = datetime.utcnow().isoformat()
        event_id = new_id()

        computed = compute_hash(parent_hash, event_type, payload, timestamp, actor)

        event = AuditEvent(
            id=event_id,
            parent_hash=parent_hash,
            hash=computed,
            event_type=event_type,
            workflow_id=workflow_id,
            payload=payload,
            llm_input=llm_input,
            llm_output=llm_output,
            actor=actor,
            timestamp=datetime.fromisoformat(timestamp),
        )

        await db.execute(
            """INSERT INTO audit_events
               (id, parent_hash, hash, event_type, workflow_id, payload, llm_input, llm_output, actor, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                event.id, event.parent_hash, event.hash, event.event_type,
                event.workflow_id, json.dumps(event.payload),
                event.llm_input, event.llm_output, event.actor,
                event.timestamp.isoformat(),
            ),
        )
        await db.commit()
        return event
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_audit_trail(workflow_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    """Retrieve audit events, optionally filtered by workflow."""
    db = await get_db()
    try:
        if workflow_id:
            rows = await db.execute_fetchall(
                "SELECT * FROM audit_events WHERE workflow_id=? ORDER BY timestamp ASC LIMIT ?",
                (workflow_id, limit),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM audit_events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def verify_chain(workflow_id: Optional[str] = None) -> dict:
    """Verify hash chain integrity. Returns {valid: bool, broken_at: str|None}."""
    db = await get_db()
    try:
        if workflow_id:
            rows = await db.execute_fetchall(
                "SELECT * FROM audit_events WHERE workflow_id=? ORDER BY timestamp ASC, id ASC",
                (workflow_id,),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM audit_events ORDER BY timestamp ASC, id ASC"
            )
        events = [dict(r) for r in rows]

        for i, ev in enumerate(events):
            expected_parent = events[i - 1]["hash"] if i > 0 else "GENESIS"
            if ev["parent_hash"] != expected_parent:
                return {"valid": False, "broken_at": ev["id"], "reason": "parent_hash mismatch"}

            payload = json.loads(ev["payload"])
            recomputed = compute_hash(
                ev["parent_hash"], ev["event_type"], payload,
                ev["timestamp"], ev["actor"]
            )
            if recomputed != ev["hash"]:
                return {"valid": False, "broken_at": ev["id"], "reason": "hash mismatch"}

        return {"valid": True, "broken_at": None, "events_checked": len(events)}
    finally:
        await db.close()
