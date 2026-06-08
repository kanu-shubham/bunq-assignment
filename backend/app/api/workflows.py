"""Workflow CRUD and action endpoints."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from app.models.domain import (
    CreateWorkflowRequest, ApprovalAction, WorkflowListItem,
    AGUIEventType,
)
from app.workflows import orchestrator
from app.services import approval_engine, audit_ledger
from app.services.sse import emit

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("/", response_model=dict)
async def create_workflow(req: CreateWorkflowRequest):
    wf = await orchestrator.create_workflow(req)
    await emit(AGUIEventType.WORKFLOW_STATE_CHANGED, wf.id, {
        "workflow_id": wf.id,
        "state": wf.state.value,
        "type": wf.type.value,
    })
    await emit(AGUIEventType.APPROVAL_REQUEST, wf.id, {
        "workflow_id": wf.id,
        "workflow_type": wf.type.value,
        "risk_score": wf.risk_score,
    })
    return wf.model_dump(mode="json")


@router.get("/", response_model=list)
async def list_workflows(
    state: str | None = Query(None),
    workflow_type: str | None = Query(None, alias="type"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    workflows = await orchestrator.list_workflows(state, workflow_type, limit, offset)
    result = []
    for wf in workflows:
        # Count pending approvals
        gate = await approval_engine.get_gate_for_workflow(wf.id)
        pending = 1 if gate and gate.status.value == "PENDING" else 0
        item = WorkflowListItem(
            id=wf.id,
            type=wf.type,
            state=wf.state,
            title=wf.title,
            risk_score=wf.risk_score,
            created_at=wf.created_at,
            updated_at=wf.updated_at,
            pending_approvals=pending,
        )
        result.append(item.model_dump(mode="json"))
    return result


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str):
    wf = await orchestrator.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    gate = await approval_engine.get_gate_for_workflow(workflow_id)
    data = wf.model_dump(mode="json")
    data["approval_gate"] = gate.model_dump(mode="json") if gate else None
    return data


@router.post("/{workflow_id}/approve")
async def approve_workflow(workflow_id: str, action: ApprovalAction):
    wf = await orchestrator.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    try:
        result = await approval_engine.submit_approval(
            workflow_id=workflow_id,
            actor=action.actor,
            role=action.role,
            decision=action.decision,
            comment=action.comment,
        )
    except approval_engine.ApprovalEngineError as e:
        raise HTTPException(400, str(e))

    event_type = (
        AGUIEventType.APPROVAL_GRANTED
        if action.decision == "approve"
        else AGUIEventType.APPROVAL_DENIED
    )
    await emit(event_type, workflow_id, {
        "actor": action.actor,
        "role": action.role.value,
        "decision": action.decision,
        "gate_status": result["gate_status"],
        "workflow_state": result["workflow_state"],
    })

    # Audit
    await audit_ledger.append_event(
        event_type=event_type.value,
        payload={
            "actor": action.actor,
            "role": action.role.value,
            "decision": action.decision,
            "comment": action.comment,
            "gate_status": result["gate_status"],
        },
        actor=action.actor,
        workflow_id=workflow_id,
    )

    return result


@router.post("/{workflow_id}/escalate")
async def escalate_workflow(workflow_id: str, body: dict):
    from app.models.domain import WorkflowState
    actor = body.get("actor", "system")
    reason = body.get("reason", "")
    try:
        wf = await orchestrator.transition_workflow(
            workflow_id, WorkflowState.ESCALATED, actor=actor, comment=reason
        )
    except orchestrator.WorkflowError as e:
        raise HTTPException(400, str(e))
    await emit(AGUIEventType.WORKFLOW_STATE_CHANGED, workflow_id, {
        "state": wf.state.value, "reason": reason
    })
    return {"workflow_id": workflow_id, "state": wf.state.value}


@router.post("/{workflow_id}/dispute")
async def dispute_workflow(workflow_id: str, body: dict):
    from app.models.domain import WorkflowState
    actor = body.get("actor", "system")
    reason = body.get("reason", "")
    try:
        wf = await orchestrator.transition_workflow(
            workflow_id, WorkflowState.DISPUTED, actor=actor, comment=reason
        )
    except orchestrator.WorkflowError as e:
        raise HTTPException(400, str(e))
    await emit(AGUIEventType.WORKFLOW_STATE_CHANGED, workflow_id, {
        "state": wf.state.value, "reason": reason
    })
    await audit_ledger.append_event(
        event_type="COUNTERPARTY_INPUT_REQUIRED",
        payload={"reason": reason},
        actor=actor,
        workflow_id=workflow_id,
    )
    return {"workflow_id": workflow_id, "state": wf.state.value}
