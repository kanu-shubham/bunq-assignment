"""SQLite database layer — async via aiosqlite."""
from __future__ import annotations
import json
import aiosqlite
import os

DB_PATH = os.environ.get("DB_PATH", "/data/hitl.db")


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                state TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT,
                created_by TEXT NOT NULL DEFAULT 'system',
                risk_score REAL NOT NULL DEFAULT 0.0,
                llm_reasoning TEXT NOT NULL DEFAULT '',
                llm_proposal TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS approval_gates (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                required_roles TEXT NOT NULL,
                quorum INTEGER NOT NULL,
                deadline TEXT,
                approvals TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(id)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY,
                parent_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                event_type TEXT NOT NULL,
                workflow_id TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                llm_input TEXT,
                llm_output TEXT,
                actor TEXT NOT NULL DEFAULT 'system',
                timestamp TEXT NOT NULL
            );
        """)
        await db.commit()
