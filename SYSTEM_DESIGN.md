# Agentic HITL Platform for Markets Operations — End-to-End System Design

> **Audience:** Staff/VP-level architecture review.
> **Scope:** A firm-wide human-in-the-loop agentic platform for settlements, reconciliations, margin-call disputes, and regulatory workflows.
> **Thesis:** *LLMs propose, rules engines decide, humans commit, ledgers remember.*

---

## 1. The Interview Frame (say this first)

When asked to design this system, open by establishing the physics — do not jump to boxes:

> "This is not a chat product. It's an operational control plane where the blast radius of a wrong action is real money, regulatory exposure, and counterparty trust. So I'm going to design around four invariants: **every irreversible action passes through a human; every decision is reconstructable years later; the deterministic layer is never overridden by the probabilistic layer; and the system survives crashes mid-workflow.** Everything else is implementation detail in service of those four."

Then drive the funnel: **Requirements → Constraints → High-level → Data model → Deep dives → Failure modes → Scale → Rollout.**

---

## 2. Requirements

### 2.1 Functional

| # | Requirement |
|---|---|
| F1 | Ingest operational events (settlement breaks, rec exceptions, margin calls, filing deadlines) from upstream systems |
| F2 | For each event, an agent produces a **proposal** (matched trade, root cause, draft response, draft filing) |
| F3 | A deterministic rules engine **validates** every proposal against limits, entitlements, sanctions, controls |
| F4 | Proposals above thresholds require **N-of-M human approval** with role gates |
| F5 | Only after approval does a **commit** action fire (post to ledger, send wire, submit filing) |
| F6 | Multi-party workflows: internal approvers, counterparties, regulators |
| F7 | Workflows are durable — they live for seconds to months |
| F8 | Every event is written to an **immutable, verifiable audit trail** |
| F9 | Operators work from a **task workbench** — queue, diff, evidence, approve/deny/escalate/dispute |
| F10 | Real-time updates pushed to all connected clients |

### 2.2 Non-functional

| Property | Target |
|---|---|
| Durability | Zero workflow loss on crash; resume from last checkpoint |
| Auditability | 100% of decisions reconstructable, including LLM I/O verbatim, retained 7+ years |
| Determinism | Identical inputs to the rules engine → identical outputs, always |
| Latency | UI interaction < 200ms; agent proposal < 30s; **workflow completion measured in T+0/T+1/T+2, not ms** |
| Availability | 99.95% for the control plane; commit adapters degrade gracefully |
| Security | Zero-trust, request signing, RBAC, PII scrubbing, segregation of duties |
| Compliance | SOC2, SOX, MiFID II, EMIR, Dodd-Frank, Basel — by construction, not bolt-on |

### 2.3 Explicit non-goals

- Not a real-time low-latency trading path (microseconds). This is the **post-trade / operations** plane.
- Not an autonomous agent that moves money. The human is structurally in the commit path.
- Not a general chatbot. Chat is one input affordance, not the product.

---

## 3. Constraints That Shape Every Decision

```
 Property               Chat product          Markets Ops platform
 ─────────────────────────────────────────────────────────────────
 Wrong-action cost      annoying              $$$ + regulatory + legal
 Reversibility          trivial               often irreversible (wire sent)
 Latency budget         sub-second            minutes → days acceptable
 Determinism need       low                   high — must replay exactly
 Workflow duration      seconds               hours → months
 Parties involved       1 user                N approvers + M counterparties + regulators
 LLM trust level        medium                LOWEST component in the stack
 "Tool call" target     a draft document      cash, positions, ledgers, filings
```

**The single most important inversion:** in a chat agent, the LLM is the orchestrator that calls tools. Here, **the LLM is demoted to an advisor at the lowest trust tier**, and a deterministic orchestrator drives everything.

---

## 4. High-Level Architecture

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                          CLIENTS  (role-aware, multi-party)                     ║
║  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐   ║
║  │ Operator       │  │ Counterparty   │  │ Mobile         │  │ Regulator    │   ║
║  │ Workbench      │  │ Portal         │  │ Approvals      │  │ Export       │   ║
║  │ React + TS     │  │ React + TS     │  │ PWA + push     │  │ (read/verify)│   ║
║  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘  └──────┬───────┘   ║
╚══════════╪═══════════════════╪═══════════════════╪══════════════════╪═══════════╝
           └──── AG-UI events over SSE/WebSocket ───┴──────────────────┘
                                  │  (REST for commands, SSE for state push)
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  EDGE / BFF                                                                      │
│  AuthN (OIDC/Entra/Okta) · AuthZ (RBAC + ABAC) · rate limit · request signing    │
│  Idempotency keys · schema validation · per-tenant routing                       │
└──────────────────────────────────────┬───────────────────────────────────────────┘
                                        ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  DURABLE WORKFLOW ORCHESTRATOR  (Temporal / LangGraph + durable checkpointer)    │
│  • Explicit state machine per workflow type                                      │
│  • OPEN → ANALYZING → AWAITING_APPROVAL → (DISPUTED|ESCALATED) → RESOLVED|REJECTED│
│  • Every transition checkpointed → crash-safe, replayable, time-travel debug     │
│  • Saga/compensation for partial failures                                        │
└──┬───────────────┬────────────────┬───────────────────┬──────────────────┬────────┘
   ▼               ▼                ▼                   ▼                  ▼
┌────────┐   ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  ┌─────────────┐
│ AGENT  │   │ RULES ENGINE │  │ TOOL         │  │ APPROVAL       │  │ EVENT BUS   │
│ LAYER  │   │ (determinist)│  │ REGISTRY     │  │ ENGINE         │  │ (SSE/Redis/ │
│        │   │              │  │              │  │                │  │  Kafka)     │
│ LLM +  │   │ limits       │  │ read         │  │ N-of-M signers │  │             │
│ RAG +  │   │ entitlements │  │ propose      │  │ role gates     │  │ AG-UI +     │
│ memory │   │ sanctions    │  │ commit ✋    │  │ quorum         │  │ finance     │
│        │   │ KYC          │  │              │  │ anti-self      │  │ custom evts │
│ ADVISES│   │ DECIDES      │  │ classifies   │  │ timeout/escal. │  │             │
│ only   │   │ never LLM    │  │ each tool    │  │ override+PIR   │  │             │
└────────┘   └──────────────┘  └──────┬───────┘  └───────┬────────┘  └─────────────┘
                                      │                   │
                                      └─────────┬─────────┘
                                                ▼
                          ┌──────────────────────────────────────────┐
                          │  APPEND-ONLY AUDIT LEDGER                │
                          │  SHA-256 hash chain · LLM I/O verbatim   │
                          │  WORM storage · regulator-exportable     │
                          │  evt₀(GENESIS) ← evt₁ ← evt₂ ← evt₃ …   │
                          └────────────────────┬─────────────────────┘
                                               ▼
                          ┌──────────────────────────────────────────┐
                          │  SIDE-EFFECT ADAPTERS  (sealed, idempotent)│
                          │  SWIFT/Fedwire · ISO 20022 · ledger ·      │
                          │  position-keeping · trade repositories     │
                          └──────────────────────────────────────────┘

   SUPPORTING PLANES (cross-cutting):
   • Identity & Secrets (HSM-backed signing keys, KMS)
   • Observability (traces, metrics, structured logs, SLA timers)
   • Reference data (counterparty/LEI, instrument master, calendars, FX rates)
   • Vector store + document store (RAG corpus: CSAs, ISDAs, rulebooks, playbooks)
```

---

## 5. Component Deep Dives

### 5.1 Durable Workflow Orchestrator (the spine)

**Why durable, not request/response:** a margin-call dispute can run for weeks. A browser tab cannot hold that. The workflow is a server-side, persisted entity with a lifecycle.

**Choice:** Temporal in production (battle-tested, deterministic replay, built-in timers/retries/sagas). LangGraph-with-checkpointer is acceptable for the agent-reasoning sub-graphs, but the **outer workflow must be Temporal** because it must outlive any LLM session and survive process restarts.

**State machine (explicit, enumerated):**
```
OPEN ──► ANALYZING ──► AWAITING_APPROVAL ──► RESOLVED
                          │   │                ▲
                          │   ├──► DISPUTED ────┤
                          │   └──► ESCALATED ───┤
                          └──────► REJECTED  (terminal)
```
Each transition is a checkpoint. A crash between any two states resumes cleanly. The transition table is the single source of truth — illegal transitions throw, they don't silently pass.

**Saga / compensation:** if a commit adapter half-succeeds (wire sent, ledger post failed), the orchestrator runs a compensating transaction and escalates — never leaves an inconsistent state.

---

### 5.2 Agent Layer (advisor, not driver)

- The agent receives a **structured task** (workflow type, payload, rules result, retrieved context).
- It returns a **typed proposal object** — never a free-text command, never a direct tool invocation.
- It can call **read** and **propose** tools (look up a position, draft an MT103). It is *architecturally incapable* of calling **commit** tools — those are not in its registry.
- The agent prompt explicitly forbids suggesting cash movement; the output schema enforces `{reasoning, proposal:{action, recommended_approvers, evidence_refs}}`.

**Sub-agents** (mirrors my Microsoft 11-agent pattern): a matcher agent, a root-cause agent, a drafting agent, a citation agent. The orchestrator routes between them; each is independently testable and observable.

---

### 5.3 Rules Engine (the decider — deterministic, no ML)

- Pure functions: `(payload) → {passed, issues[], risk_score}`. Side-effect free, synchronous, unit-tested to exhaustion.
- Encodes hard controls: position/notional limits, entitlements, sanctions screening (OFAC/UN), KYC, currency allow-lists, threshold-based escalation.
- **Runs twice:** once before the agent proposes (to constrain it) and again at commit time (to re-validate against current state — never trust a stale check).
- The LLM can be wrong; the rules engine cannot. They are physically separate services so the boundary can never blur.

---

### 5.4 Tool Registry & Classification

Every tool is tagged at registration:
```
read    → free to call (lookups, projections)            no approval
propose → builds a draft action object                   no approval
commit  → causes a real side effect                       ALWAYS approval-gated
```
Commit tools carry an `approval_policy` (e.g., `2-of-3 {Treasury, Compliance, Ops}`, `maxAmount`). The orchestrator — not the agent — is the only caller of commit tools, and only after the approval gate resolves.

---

### 5.5 Approval Engine (the human commit gate)

A standalone horizontal service, never buried inside workflow code. Enforces:

1. **Role gate** — signer's role ∈ required roles.
2. **Anti-self-approval** — signer ≠ proposer (segregation of duties; SOX).
3. **No duplicate votes** — one decision per actor per gate.
4. **N-of-M quorum** — configurable per workflow type and amount band.
5. **Immediate denial** — a single deny rejects the workflow.
6. **Timeout → auto-escalation** — SLA timer; unresolved gates escalate up the chain.
7. **Override path** — break-glass with mandatory justification + post-incident review.

Policies are data, not code:
```
SETTLEMENT          → {OPERATIONS, RISK}        quorum 2
CROSS_BORDER_SETTLE → {TREASURY, COMPLIANCE, OPS} quorum 2-of-3, maxAmount 50M
DISPUTE / MARGIN    → {RISK, LEGAL}             quorum 2
REGULATORY_FILING   → {COMPLIANCE, LEGAL}       quorum 2
```

---

### 5.6 Audit Ledger (the memory — long-term, immutable)

- Append-only, SHA-256 hash-chained: `hash = SHA256(parent_hash | type | payload | ts | actor)`.
- Stores **every** event: creation, transitions, rules results, approvals/denials, overrides, and **LLM prompt + completion verbatim** (PII-scrubbed).
- Tamper-evident: altering event *n* breaks every hash after it. A `/verify` endpoint returns a cryptographic proof of integrity.
- Production storage: WORM object store (e.g., S3 Object Lock) + indexed Postgres for query; or QLDB. Retained 7+ years.
- This is the artifact you put in front of a regulator three years later to defend an automated decision.

---

### 5.7 Event Bus & AG-UI Protocol (the nervous system)

- Adopt **AG-UI** as the wire protocol — don't reinvent. Standard events: `RUN_STARTED`, `TEXT_MESSAGE_*`, `TOOL_CALL_*`, `STATE_SNAPSHOT/DELTA`, `INTERRUPT`, `RESUME`.
- Extend with **typed finance custom events**:
  `APPROVAL_REQUEST · APPROVAL_GRANTED · APPROVAL_DENIED · OVERRIDE_INVOKED · COUNTERPARTY_INPUT_REQUIRED · REG_FILING_DRAFTED · EVIDENCE_PINNED`.
- Transport: SSE for server→client push (simpler, auto-reconnect, fits the workbench). WebSocket only where bidirectional is genuinely needed (counterparty co-editing). Fan-out via Redis pub/sub or Kafka in production; in-memory queues for single-node dev.

---

## 6. Memory Architecture (the part most candidates miss)

```
SHORT-TERM   context window        1 request        the agent's prompt + retrieved docs
MEDIUM-TERM  workflow state        hours→months     durable orchestrator checkpoints, approval votes
LONG-TERM    audit ledger          forever          every event, LLM I/O, hash-chained
RAG CONTEXT  vector + doc store    until re-index    CSAs, ISDAs, rulebooks, past resolutions, playbooks
```

- **Short-term** is rebuilt every invocation — the agent is stateless by design (reproducibility).
- **Medium-term** is the durable workflow; it is what makes weeks-long disputes possible.
- **Long-term** is the ledger — write-once, read-forever.
- **RAG** retrieves at proposal time: "last time we had a EUR/CHF break, ops adjusted accruals by $50"; "CSA Clause 11.3 sets the pricing tolerance"; "EMIR Refit requires UTI within T+1." The corpus is the audit ledger's resolved cases + legal docs + regulatory rulebooks, embedded into a vector store (pgvector/Pinecone), with the retrieved chunks themselves logged for auditability.

---

## 7. Data Model (canonical entities)

```
WorkflowInstance { id, type, state, payload, risk_score, llm_reasoning,
                   llm_proposal, created_by, created_at, updated_at, resolved_at }

ApprovalGate     { id, workflow_id, required_roles[], quorum, deadline,
                   approvals[ {actor, role, decision, comment, ts} ], status }

AuditEvent       { id, parent_hash, hash, event_type, workflow_id, payload,
                   llm_input, llm_output, actor, timestamp }

Tool             { name, tool_class(read|propose|commit), approval_policy, allowed_roles[] }

EvidenceRef      { id, workflow_id, document_hash, storage_ref, classification }

FinanceEvent     (discriminated union on event_type — one shape Python→wire→TS)
```

One schema definition flows backend (Pydantic) → SSE wire (JSON) → frontend (TypeScript discriminated unions). No drift.

---

## 8. The Frontend — A Workbench, Not a Chatbox

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Top bar:  [⚡ HITL Workbench]   Acting as: [Risk ▾]      [+ New Workflow]  │
├───────────────┬────────────────────────────────────────────────────────────┤
│ TASK QUEUE    │  WORKFLOW DETAIL                                            │
│ (left rail)   │                                                            │
│ ▸ 12 approvals│  ┌─ State machine viz:  OPEN→AWAITING→… (current pulsing) ┐ │
│ ▸ 3 disputes  │  ┌─ Approval card:  risk score · LLM reasoning · evidence ┐ │
│ ▸ 1 reg filing│  │   N-of-M progress bar  [✓Approve][✗Deny][↑Esc][⚡Disp]│ │
│   sorted by   │  ┌─ Diff viewer:  proposed change vs current state        ┐ │
│   risk × SLA  │  ┌─ Evidence panel:  pinned docs, hashes, sources          ┐ │
│               │  ┌─ Audit trail:  hash-chained event log + verify badge    ┐ │
└───────────────┴────────────────────────────────────────────────────────────┘
```

**Design principles (for the Design round):**
- **Triage-first IA:** an operator lands on a prioritized queue (risk × SLA), not a blank prompt.
- **Decision in <10s:** the approval card surfaces *exactly* what's needed — what changed, why the agent recommends it, what rule flagged it, who else signed.
- **Diff over prose:** structured before/after, not a paragraph to read.
- **Defense in depth in the UI:** the client enforces anti-self-approval and role gates too — same guard as the backend, so the user never sees an action they can't take.
- **Role-aware rendering:** a Trader, Risk officer, and Compliance officer see different affordances on the same workflow.
- **Streaming via AG-UI:** the queue and detail update live as events arrive; no manual refresh.

**Stack:** React + TypeScript, AG-UI client (or CopilotKit/Vercel AI SDK for the agent-chat affordance), TanStack Query for server state, a state machine (XState) for the client-side workflow mirror, design-system components for regulated-environment consistency and WCAG compliance.

---

## 9. Failure Modes & How the Design Survives Them

| Failure | Mitigation |
|---|---|
| Orchestrator crashes mid-workflow | Durable checkpoints → resume from last state, no loss |
| Commit adapter half-succeeds | Saga compensation + escalate; never inconsistent |
| LLM hallucinates a bad proposal | Rules engine rejects it; human never sees an invalid commit; worst case = wasted proposal |
| LLM unavailable | Workflow degrades to manual review — humans still operate, just without the assist |
| Approver unavailable / SLA breach | Timer auto-escalates up the chain |
| Audit tampering attempt | Hash chain breaks; `/verify` flags exact event |
| Duplicate command (network retry) | Idempotency keys at the BFF |
| Stale rules check | Re-validate at commit, not just at propose |
| Counterparty doesn't respond | `COUNTERPARTY_INPUT_REQUIRED` with deadline → escalation |

---

## 10. Security & Compliance by Construction

- **Zero-trust:** every call authenticated, authorized, signed; commit actions signed with HSM-backed keys.
- **Segregation of duties:** anti-self-approval + role gates enforce SOX.
- **Least privilege:** the agent's tool registry literally excludes commit tools.
- **PII scrubbing** before anything hits the LLM or the ledger's queryable index.
- **Data residency / tenancy:** per-jurisdiction routing at the BFF (MiFID II vs Dodd-Frank).
- **Full lineage:** every decision → who/what/when/why, exportable for SOC2/audit.

---

## 11. Scale & Evolution

- **Horizontal:** orchestrator workers scale out; Temporal shards by workflow ID. Event bus → Kafka. Audit index → partitioned Postgres + WORM cold store.
- **Throughput reality:** ops volume is thousands–tens-of-thousands of workflows/day, not millions/sec. The bottleneck is **human approval bandwidth**, not compute — so the design optimizes operator decision latency, not raw QPS.
- **Multi-region:** active-active control plane; commit adapters region-pinned to the relevant market infrastructure.
- **Model evolution:** the agent layer is swappable behind the proposal interface — upgrade models without touching the orchestrator, rules, or ledger.

---

## 12. Rollout Strategy (how I'd ship it at a bank)

1. **Shadow mode** — agent proposes, humans do everything manually; we measure proposal quality against human decisions. No commit path wired.
2. **Assist mode** — proposals shown in the workbench; humans approve/commit. Single low-risk workflow first (e.g., rec exceptions under a small threshold).
3. **Guarded autonomy** — auto-commit for the lowest-risk, fully-deterministic cases (small recs within tolerance), everything else still human-gated.
4. **Expand by workflow type** — settlement → dispute → margin call → regulatory, each gated behind metrics: proposal acceptance rate, false-positive rate, time-to-resolution, zero audit gaps.

Never big-bang. Earn trust per workflow, backed by the audit ledger as evidence.

---

## 13. The One-Paragraph Summary (close with this)

> A durable workflow orchestrator is the spine. The LLM is a swappable advisor at the lowest trust tier — it proposes typed actions and can only call read/propose tools. A deterministic rules engine decides validity and never yields to the model. Commit tools are gated behind an N-of-M approval engine with anti-self-approval and timeout-escalation. Every event — including every LLM prompt and completion — lands in a SHA-256 hash-chained, regulator-verifiable ledger. The frontend is a role-aware triage workbench, driven by AG-UI events, where an operator makes an informed approve/deny decision in under ten seconds. **LLMs propose, rules decide, humans commit, ledgers remember** — and we roll it out in shadow → assist → guarded-autonomy, earning trust one workflow at a time.
