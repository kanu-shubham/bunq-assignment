"""Tool registry endpoint."""
from fastapi import APIRouter
from app.models.domain import ToolDefinition, ToolClass, UserRole

router = APIRouter(prefix="/tools", tags=["tools"])

TOOL_REGISTRY: list[ToolDefinition] = [
    ToolDefinition(
        name="get_workflow_details",
        description="Read full workflow details including payload and LLM proposal",
        tool_class=ToolClass.READ,
        allowed_roles=list(UserRole),
    ),
    ToolDefinition(
        name="list_workflows",
        description="List all workflows with filtering",
        tool_class=ToolClass.READ,
        allowed_roles=list(UserRole),
    ),
    ToolDefinition(
        name="get_audit_trail",
        description="Read the append-only audit ledger for a workflow",
        tool_class=ToolClass.READ,
        allowed_roles=list(UserRole),
    ),
    ToolDefinition(
        name="propose_settlement",
        description="LLM proposes a settlement resolution (does not commit)",
        tool_class=ToolClass.PROPOSE,
        approval_policy="1-of-2",
        allowed_roles=[UserRole.TRADER, UserRole.OPERATIONS, UserRole.RISK],
    ),
    ToolDefinition(
        name="propose_dispute_response",
        description="LLM drafts a dispute response letter (does not send)",
        tool_class=ToolClass.PROPOSE,
        approval_policy="2-of-3",
        allowed_roles=[UserRole.RISK, UserRole.LEGAL],
    ),
    ToolDefinition(
        name="propose_reg_filing",
        description="LLM drafts a regulatory filing (does not submit)",
        tool_class=ToolClass.PROPOSE,
        approval_policy="2-of-2",
        allowed_roles=[UserRole.COMPLIANCE, UserRole.LEGAL],
    ),
    ToolDefinition(
        name="commit_settlement",
        description="Commit approved settlement to ledger — requires quorum approval",
        tool_class=ToolClass.COMMIT,
        approval_policy="2-of-2",
        allowed_roles=[UserRole.OPERATIONS, UserRole.RISK],
    ),
    ToolDefinition(
        name="commit_regulatory_filing",
        description="Submit approved regulatory filing to trade repository",
        tool_class=ToolClass.COMMIT,
        approval_policy="2-of-2",
        allowed_roles=[UserRole.COMPLIANCE, UserRole.LEGAL],
    ),
    ToolDefinition(
        name="send_wire_transfer",
        description="Execute approved SWIFT wire transfer — highest approval requirement",
        tool_class=ToolClass.COMMIT,
        approval_policy="2-of-3",
        allowed_roles=[UserRole.OPERATIONS, UserRole.RISK, UserRole.ADMIN],
    ),
]


@router.get("/")
async def list_tools():
    return [t.model_dump(mode="json") for t in TOOL_REGISTRY]
