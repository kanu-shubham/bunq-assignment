"""
Workflow orchestrator — durable state machine with SQLite-backed checkpoints.
Explicit transitions: OPEN → AWAITING_APPROVAL → (RESOLVED | REJECTED | DISPUTED | ESCALATED)
LLM proposes; rules validate; humans commit.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any, Optional
import aiosqlite

from app.models.domain import (
    WorkflowInstance, WorkflowState, WorkflowType,
    CreateWorkflowRequest, new_id,
)
from app.services.database import get_db
from app.rules.engine import run_rules
from app.services.llm_advisory import get_proposal
from app.services import audit_ledger
from app.services import approval_engine


# Valid state transitions
TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.OPEN: {WorkflowState.AWAITING_APPROVAL, WorkflowState.DISPUTED},
    WorkflowState.AWAITING_APPROVAL: {
        WorkflowState.RESOLVED, WorkflowState.REJECTED,
        WorkflowState.DISPUTED, WorkflowState.ESCALATED,
    },
    WorkflowState.DISPUTED: {WorkflowState.AWAITING_APPROVAL, WorkflowState.ESCALATED},
    WorkflowState.ESCALATED: {WorkflowState.RESOLVED, WorkflowState.REJECTED},
    WorkflowState.RESOLVED: set(),
    WorkflowState.REJECTED: set(),
}


class WorkflowError(Exception):
    pass


async def create_workflow(req: CreateWorkflowRequest) -> WorkflowInstance:
    """Create a new workflow, run rules + LLM, create approval gate."""
    # Run rules engine (deterministic, synchronous)
    rules_result = run_rules(req.type.value, req.payload)

    # LLM advisory (async, mock or real)
    llm_result = await get_proposal(req.type.value, req.payload, rules_result)

    wf = WorkflowInstance(
        type=req.type,
        state=WorkflowState.OPEN,
        title=req.title,
        description=req.description,
        payload=req.payload,
        created_by=req.created_by,
        risk_score=rules_result["risk_score"],
        llm_reasoning=llm_result["reasoning"],
        llm_proposal=llm_result["proposal"],
    )

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO workflows
               (id, type, state, title, description, payload,
                created_at, updated_at, resolved_at, created_by,
                risk_score, llm_reasoning, llm_proposal)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                wf.id, wf.type.value, wf.state.value, wf.title, wf.description,
                json.dumps(wf.payload), wf.created_at.isoformat(),
                wf.updated_at.isoformat(), None,
                wf.created_by, wf.risk_score, wf.llm_reasoning,
                json.dumps(wf.llm_proposal),
            ),
        )
        await db.commit()
    finally:
        await db.close()

    # Audit
    await audit_ledger.append_event(
        event_type="WORKFLOW_CREATED",
        payload={
            "workflow_type": req.type.value,
            "title": req.title,
            "risk_score": rules_result["risk_score"],
            "rules_issues": rules_result["issues"],
            "llm_proposal_action": llm_result["proposal"].get("action"),
        },
        actor=req.created_by,
        workflow_id=wf.id,
        llm_input=llm_result.get("llm_input"),
        llm_output=llm_result.get("llm_output"),
    )

    # Advance to AWAITING_APPROVAL and create gate
    await transition_workflow(wf.id, WorkflowState.AWAITING_APPROVAL, actor=req.created_by)
    await approval_engine.create_approval_gate(wf.id, req.type.value)

    # Audit the approval request
    await audit_ledger.append_event(
        event_type="APPROVAL_REQUEST",
        payload={"workflow_id": wf.id, "workflow_type": req.type.value},
        actor="system",
        workflow_id=wf.id,
    )

    wf.state = WorkflowState.AWAITING_APPROVAL
    return wf


async def transition_workflow(
    workflow_id: str,
    new_state: WorkflowState,
    actor: str = "system",
    comment: str = "",
) -> WorkflowInstance:
    wf = await get_workflow(workflow_id)
    if wf is None:
        raise WorkflowError(f"Workflow {workflow_id} not found")

    if new_state not in TRANSITIONS.get(wf.state, set()):
        raise WorkflowError(
            f"Invalid transition {wf.state.value} → {new_state.value}"
        )

    resolved_at = None
    if new_state in (WorkflowState.RESOLVED, WorkflowState.REJECTED):
        resolved_at = datetime.utcnow().isoformat()

    db = await get_db()
    try:
        await db.execute(
            "UPDATE workflows SET state=?, updated_at=?, resolved_at=? WHERE id=?",
            (new_state.value, datetime.utcnow().isoformat(), resolved_at, workflow_id),
        )
        await db.commit()
    finally:
        await db.close()

    await audit_ledger.append_event(
        event_type="WORKFLOW_STATE_CHANGED",
        payload={
            "from_state": wf.state.value,
            "to_state": new_state.value,
            "comment": comment,
        },
        actor=actor,
        workflow_id=workflow_id,
    )

    wf.state = new_state
    return wf


async def get_workflow(workflow_id: str) -> Optional[WorkflowInstance]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM workflows WHERE id=?", (workflow_id,)
        )
        if not rows:
            return None
        return _row_to_workflow(dict(rows[0]))
    finally:
        await db.close()


async def list_workflows(
    state: Optional[str] = None,
    workflow_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[WorkflowInstance]:
    db = await get_db()
    try:
        clauses = []
        params: list[Any] = []
        if state:
            clauses.append("state=?")
            params.append(state)
        if workflow_type:
            clauses.append("type=?")
            params.append(workflow_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = await db.execute_fetchall(
            f"SELECT * FROM workflows {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        )
        return [_row_to_workflow(dict(r)) for r in rows]
    finally:
        await db.close()


def _row_to_workflow(row: dict) -> WorkflowInstance:
    return WorkflowInstance(
        id=row["id"],
        type=WorkflowType(row["type"]),
        state=WorkflowState(row["state"]),
        title=row["title"],
        description=row["description"],
        payload=json.loads(row["payload"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        created_by=row["created_by"],
        risk_score=row["risk_score"],
        llm_reasoning=row["llm_reasoning"],
        llm_proposal=json.loads(row["llm_proposal"]) if row["llm_proposal"] else {},
    )
