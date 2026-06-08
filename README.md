# HITL Financial Workflow System

A Human-in-the-Loop (HITL) stack for financial workflows covering settlements, reconciliations, disputes, margin calls, and regulatory submissions.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (React)                         │
│  Task Queue │ Diff Viewer │ Approval Card │ Audit Trail │ SM Viz │
└──────────────────────────┬──────────────────────────────────────┘
                           │  REST + SSE (AG-UI events)
┌──────────────────────────▼──────────────────────────────────────┐
│                     FastAPI Backend                             │
│                                                                 │
│  Workflow Orchestrator (state machine)                          │
│  Rules Engine (pure Python, deterministic)                      │
│  LLM Advisory (mock/Claude — PROPOSE only, never COMMIT)       │
│  Approval Engine (N-of-M, role gates, anti-self-approval)      │
│  Audit Ledger (append-only, SHA-256 hash chain)                │
│  SSE Event Bus (AG-UI protocol + finance HITL extensions)      │
│                                                                 │
│  SQLite (WAL mode) — swappable to Postgres                     │
└─────────────────────────────────────────────────────────────────┘
```

## Core Principles

1. **LLMs propose; rules engines decide; humans commit** — The LLM layer never directly causes cash movement. All proposals go through the rules engine and require human approval.

2. **Durable workflows** — Workflow state is persisted to SQLite with full history. Workflows outlive browser sessions and are replayable from the audit ledger.

3. **AG-UI event protocol** — Extended with finance-specific HITL events: `APPROVAL_REQUEST`, `APPROVAL_GRANTED`, `APPROVAL_DENIED`, `OVERRIDE_INVOKED`, `COUNTERPARTY_INPUT_REQUIRED`, `REG_FILING_DRAFTED`, `EVIDENCE_PINNED`.

4. **N-of-M approval engine** — Separate service with role-based gates, quorum policies, deadline+escalation, and anti-self-approval enforcement.

5. **Append-only hash-chained audit ledger** — Each event has a `parent_hash` (SHA-256), forming a tamper-evident chain. Integrity verifiable via `/api/audit/verify`.

6. **Tool classification** — All tools are classified as `read`, `propose`, or `commit`. Only `commit`-class tools cause state changes, and only after approval.

7. **UI is a workbench, not a chatbox** — Task queue, diff viewer, approval card with N-of-M gate, audit trail timeline, and state machine visualizer.

## Workflow State Machine

```
OPEN → AWAITING_APPROVAL → RESOLVED
                        → REJECTED
                        → DISPUTED → AWAITING_APPROVAL
                                   → ESCALATED → RESOLVED
                                              → REJECTED
```

## Workflow Types

| Type | Description | Approval Policy |
|------|-------------|-----------------|
| SETTLEMENT | T+1 settlement break, SWIFT MT103 | OPERATIONS + RISK (2-of-2) |
| RECONCILIATION | Daily rec exceptions | OPERATIONS (1-of-1) |
| DISPUTE | Counterparty margin call disputes | RISK + LEGAL (2-of-2) |
| MARGIN_CALL | VaR-triggered margin calls | RISK + LEGAL (2-of-2) |
| REGULATORY_FILING | EMIR/MiFID2/SFTR filings | COMPLIANCE + LEGAL (2-of-2) |

## Running Locally

### With Docker Compose (recommended)

```bash
# Start backend + frontend
docker compose up

# Seed demo data (separate terminal)
docker compose --profile seed run seed

# Access:
# Frontend: http://localhost:3000
# Backend API: http://localhost:8000
# API docs: http://localhost:8000/docs
```

### Backend only (dev)

```bash
cd backend
pip install -r requirements.txt
DB_PATH=./hitl.db uvicorn app.main:app --reload
```

### Frontend only (dev)

```bash
npm install --legacy-peer-deps
REACT_APP_API_URL=http://localhost:8000/api npm start
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/workflows/` | List workflows (filterable) |
| POST | `/api/workflows/` | Create workflow (triggers rules + LLM) |
| GET | `/api/workflows/{id}` | Get workflow + approval gate |
| POST | `/api/workflows/{id}/approve` | Submit approval/denial |
| POST | `/api/workflows/{id}/escalate` | Escalate workflow |
| POST | `/api/workflows/{id}/dispute` | Mark as disputed |
| GET | `/api/audit/` | Get audit trail |
| GET | `/api/audit/verify` | Verify hash chain integrity |
| GET | `/api/events/stream` | SSE event stream (AG-UI) |
| GET | `/api/tools/` | Tool registry (read/propose/commit) |

## Security Properties

- **Anti-self-approval**: The actor who created a workflow cannot approve it.
- **Role gates**: Only approved roles can vote on each gate type.
- **Quorum enforcement**: Configurable N-of-M quorum per workflow type.
- **Audit immutability**: Hash chain ensures ledger tampering is detectable.
- **LLM isolation**: LLM output is advisory only; classified as `propose`, never `commit`.

## Data Models

### WorkflowInstance
```
id, type, state, title, description, payload,
created_at, updated_at, resolved_at, created_by,
risk_score, llm_reasoning, llm_proposal
```

### ApprovalGate
```
id, workflow_id, required_roles[], quorum,
deadline, approvals[], status, created_at
```

### AuditEvent
```
id, parent_hash, hash (SHA-256), event_type,
workflow_id, payload, llm_input, llm_output,
actor, timestamp
```

## LLM Mock Mode

By default `LLM_MOCK=true` — canned responses are returned for each workflow type. Set `LLM_MOCK=false` and provide `ANTHROPIC_API_KEY` to use real Claude API.
