"""N-of-M approval engine with role gates, anti-self-approval, quorum, and timeout."""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from typing import Optional
import aiosqlite
from app.models.domain import (
    ApprovalGate, ApprovalRecord, ApprovalStatus, UserRole,
    WorkflowState, new_id,
)
from app.services.database import get_db


# Role quorum policies per workflow type
QUORUM_POLICIES: dict[str, dict] = {
    "SETTLEMENT": {"required_roles": ["OPERATIONS", "RISK"], "quorum": 2},
    "RECONCILIATION": {"required_roles": ["OPERATIONS"], "quorum": 1},
    "DISPUTE": {"required_roles": ["RISK", "LEGAL"], "quorum": 2},
    "MARGIN_CALL": {"required_roles": ["RISK", "LEGAL"], "quorum": 2},
    "REGULATORY_FILING": {"required_roles": ["COMPLIANCE", "LEGAL"], "quorum": 2},
}


async def create_approval_gate(
    workflow_id: str,
    workflow_type: str,
    deadline_hours: int = 24,
) -> ApprovalGate:
    policy = QUORUM_POLICIES.get(workflow_type, {"required_roles": ["OPERATIONS"], "quorum": 1})
    gate = ApprovalGate(
        workflow_id=workflow_id,
        required_roles=[UserRole(r) for r in policy["required_roles"]],
        quorum=policy["quorum"],
        deadline=datetime.utcnow() + timedelta(hours=deadline_hours),
    )
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO approval_gates
               (id, workflow_id, required_roles, quorum, deadline, approvals, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                gate.id, gate.workflow_id,
                json.dumps([r.value for r in gate.required_roles]),
                gate.quorum,
                gate.deadline.isoformat() if gate.deadline else None,
                json.dumps([]),
                gate.status.value,
                gate.created_at.isoformat(),
            ),
        )
        await db.commit()
    finally:
        await db.close()
    return gate


async def get_gate(gate_id: str) -> Optional[ApprovalGate]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM approval_gates WHERE id=?", (gate_id,)
        )
        if not rows:
            return None
        return _row_to_gate(dict(rows[0]))
    finally:
        await db.close()


async def get_gate_for_workflow(workflow_id: str) -> Optional[ApprovalGate]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM approval_gates WHERE workflow_id=? AND status='PENDING' ORDER BY created_at DESC LIMIT 1",
            (workflow_id,),
        )
        if not rows:
            return None
        return _row_to_gate(dict(rows[0]))
    finally:
        await db.close()


def _row_to_gate(row: dict) -> ApprovalGate:
    approvals_raw = json.loads(row["approvals"])
    approvals = [ApprovalRecord(**a) for a in approvals_raw]
    return ApprovalGate(
        id=row["id"],
        workflow_id=row["workflow_id"],
        required_roles=[UserRole(r) for r in json.loads(row["required_roles"])],
        quorum=row["quorum"],
        deadline=datetime.fromisoformat(row["deadline"]) if row["deadline"] else None,
        approvals=approvals,
        status=ApprovalStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class ApprovalEngineError(Exception):
    pass


async def submit_approval(
    workflow_id: str,
    actor: str,
    role: UserRole,
    decision: str,
    comment: str = "",
) -> dict:
    """
    Submit an approval or denial.
    Enforces:
    - Anti-self-approval: actor cannot approve their own workflow
    - Role gate: actor's role must be in required_roles
    - No duplicate votes from same actor
    - Quorum check: when enough approvals, mark gate approved
    - Denial propagates immediately
    """
    gate = await get_gate_for_workflow(workflow_id)
    if gate is None:
        raise ApprovalEngineError(f"No pending approval gate for workflow {workflow_id}")

    if gate.status != ApprovalStatus.PENDING:
        raise ApprovalEngineError(f"Gate {gate.id} is already {gate.status.value}")

    # Check deadline
    if gate.deadline and datetime.utcnow() > gate.deadline:
        await _update_gate_status(gate.id, ApprovalStatus.EXPIRED)
        raise ApprovalEngineError("Approval gate has expired")

    # Role gate
    if role not in gate.required_roles:
        raise ApprovalEngineError(
            f"Role {role.value} not in required roles {[r.value for r in gate.required_roles]}"
        )

    # Anti-self-approval: check if actor created the workflow
    db = await get_db()
    try:
        wf_rows = await db.execute_fetchall(
            "SELECT created_by FROM workflows WHERE id=?", (workflow_id,)
        )
        if wf_rows and wf_rows[0]["created_by"] == actor:
            raise ApprovalEngineError("Anti-self-approval: actor cannot approve their own workflow")
    finally:
        await db.close()

    # Duplicate vote check
    existing_actors = {a.actor for a in gate.approvals}
    if actor in existing_actors:
        raise ApprovalEngineError(f"Actor {actor} has already voted on gate {gate.id}")

    record = ApprovalRecord(
        actor=actor, role=role, decision=decision, comment=comment
    )
    gate.approvals.append(record)

    # Evaluate outcome
    approve_count = sum(1 for a in gate.approvals if a.decision == "approve")
    deny_count = sum(1 for a in gate.approvals if a.decision == "deny")

    new_status = ApprovalStatus.PENDING
    new_workflow_state: Optional[WorkflowState] = None

    if decision == "deny":
        new_status = ApprovalStatus.DENIED
        new_workflow_state = WorkflowState.REJECTED
    elif approve_count >= gate.quorum:
        new_status = ApprovalStatus.APPROVED
        new_workflow_state = WorkflowState.RESOLVED

    # Persist
    db = await get_db()
    try:
        await db.execute(
            "UPDATE approval_gates SET approvals=?, status=? WHERE id=?",
            (
                json.dumps([a.model_dump(mode="json") for a in gate.approvals]),
                new_status.value,
                gate.id,
            ),
        )
        if new_workflow_state:
            resolved_at = datetime.utcnow().isoformat() if new_workflow_state in (
                WorkflowState.RESOLVED, WorkflowState.REJECTED
            ) else None
            await db.execute(
                "UPDATE workflows SET state=?, updated_at=?, resolved_at=? WHERE id=?",
                (new_workflow_state.value, datetime.utcnow().isoformat(), resolved_at, workflow_id),
            )
        await db.commit()
    finally:
        await db.close()

    return {
        "gate_id": gate.id,
        "gate_status": new_status.value,
        "workflow_state": new_workflow_state.value if new_workflow_state else None,
        "approve_count": approve_count if decision == "approve" else approve_count,
        "quorum": gate.quorum,
        "approvals": [a.model_dump(mode="json") for a in gate.approvals],
    }


async def _update_gate_status(gate_id: str, status: ApprovalStatus):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE approval_gates SET status=? WHERE id=?",
            (status.value, gate_id),
        )
        await db.commit()
    finally:
        await db.close()
