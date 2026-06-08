"""Audit trail endpoints."""
from fastapi import APIRouter, Query
from app.services import audit_ledger

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/")
async def get_audit_trail(
    workflow_id: str | None = Query(None),
    limit: int = Query(200, le=1000),
):
    events = await audit_ledger.get_audit_trail(workflow_id=workflow_id, limit=limit)
    return events


@router.get("/verify")
async def verify_chain(workflow_id: str | None = Query(None)):
    result = await audit_ledger.verify_chain(workflow_id=workflow_id)
    return result
