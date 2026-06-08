# Deep Technical Topics — Interview Prep
## Checkpointing · Audit/Ledger/Rules · Financial Failure Modes · Performance

---

# PART 1 — How Checkpointing Works in Real Apps

## The core idea

A checkpoint is a **durable snapshot of a workflow's state at a point in its execution**, written to persistent storage, so that if the process crashes, restarts, or scales down, the workflow resumes from exactly where it left off — not from the beginning.

> "In a chat app, if the server dies mid-request you retry the request. In an operations platform, a workflow might be three days into a margin-call dispute with two of three approvals collected. You cannot 'retry from the start' — that would re-collect approvals, re-send notices, maybe re-send a wire. Checkpointing is what makes a long-running, multi-step, human-gated workflow survivable."

## What gets checkpointed

```
At each significant step, persist:
  • current state node          (AWAITING_APPROVAL)
  • accumulated context         (LLM proposal, rules result, gathered evidence)
  • approval votes so far        (1 of 2 collected)
  • position in the graph        (which node, which edge taken)
  • a monotonic version/sequence (for ordering + optimistic concurrency)
```

## How it works in LangGraph (what you'd say)

> "LangGraph models the workflow as a graph — nodes are steps, edges are transitions. You attach a **checkpointer** (SQLite for dev, Postgres for prod). After *every node executes*, the checkpointer writes the full graph state to the store, keyed by a thread id. To resume, LangGraph loads the latest checkpoint for that thread and continues from the next node. Because the state is serialised at each node, you also get **time-travel**: you can load checkpoint N, inspect it, even fork execution from there for debugging."

```
START → analyze ──✓checkpoint──→ propose ──✓checkpoint──→ await_approval ──✓checkpoint──→ commit
                                                              │
                                            [CRASH HERE]      │
                                                              ▼
                              on restart: load latest checkpoint (await_approval)
                              resume waiting for the 2nd approval — votes intact
```

## How it works in Temporal (the production-grade version)

> "Temporal takes a different, stronger approach called **event sourcing / deterministic replay**. It doesn't snapshot state — it records every event (every step result, every signal) in a history. On recovery, it *replays* the workflow code against that history to reconstruct in-memory state exactly. The workflow code must be deterministic — no `random()`, no `now()` directly; side effects go through 'activities' which are recorded. The payoff: the workflow can run for months, survive any number of restarts and deploys, and resume bit-for-bit correct. That's why I'd choose Temporal for the durable outer workflow in this platform."

## The key distinctions to articulate

| Approach | Mechanism | Trade-off |
|---|---|---|
| **State snapshot** (LangGraph checkpointer) | Serialise full state after each node | Simple; state can be large; good for agent sub-graphs |
| **Event sourcing / replay** (Temporal) | Record events, replay code to rebuild state | Requires deterministic code; extremely robust; ideal for long workflows |
| **DB transaction per step** (our demo) | Each transition is a committed DB row | Simplest; you manually manage resume logic |

## Why it matters for HITL specifically

> "Human steps are *slow and unbounded* — an approver might take three days. You cannot hold that in memory or in a request. The workflow must be persisted and the process freed. When the approval finally arrives, it's a *signal* that wakes the checkpointed workflow. Checkpointing is precisely what lets you have a workflow that's 'paused on a human' without consuming resources while it waits."

## Idempotency — the checkpointing companion

> "Checkpointing and idempotency go together. When you resume from a checkpoint, you might re-execute the step that was in flight when you crashed. If that step was 'send wire', re-executing it double-sends. So every side-effecting step carries an idempotency key — `wire:{workflow_id}` — and the adapter dedupes. Resume becomes safe: at-least-once execution, exactly-once effect."

---

# PART 2 — Audit, Ledger, and Rules Explained

## 2.1 The Rules Engine

**What it is:** a set of pure, deterministic functions that validate a proposed action against the firm's hard controls. No ML, no randomness, no LLM.

**What it checks (per workflow type):**
```
SETTLEMENT:        positive amount · ≤ single-trade limit · approved currency
                   · counterparty present · sanctions screen
SWIFT MT103:       all mandatory fields · valid value_date format
RECONCILIATION:    break ≤ escalation threshold · tolerance in [0, 5%]
DISPUTE:           dispute_reason present · original_trade_id referenced
MARGIN_CALL:       positive call · ≤ senior-approval threshold · portfolio_id present
REGULATORY_FILING: supported regime (EMIR/MiFID2/...) · trade repository · UTI present
```

**Why it's separate from the LLM (the central point):**
> "The LLM is allowed to be wrong — it's probabilistic. The rules engine is *not* allowed to be wrong — it's deterministic and auditable. So they are physically separate services. The LLM proposes; the rules engine decides validity. If the rules engine says a counterparty is sanctioned, no amount of LLM confidence overrides it. And critically, the rules engine runs **twice**: once before the agent proposes, to constrain it, and again at commit time, to re-validate against current state — because the world may have changed while the workflow waited on a human."

**Risk scoring:** a deterministic 0–1 heuristic (amount/limit ratios, cross-border flag, disputed flag) — used to prioritise the operator's queue, never to make the decision.

## 2.2 The Audit Ledger

**What it is:** an append-only, hash-chained, immutable record of every event in the system.

**The hash chain:**
```
hash(n) = SHA-256( parent_hash | event_type | payload | timestamp | actor )

GENESIS ← evt₁ ← evt₂ ← evt₃ ← evt₄ ← …
            each event embeds the previous event's hash
```

**Why hash-chained:** tamper-evidence. Alter event₂'s payload → its hash changes → evt₃'s `parent_hash` no longer matches → the chain breaks at a detectable point. You cannot quietly rewrite history.

**What's stored verbatim:** every workflow creation, every state transition (who, when, why), every approval/denial with role and comment, every rule violation, and — non-negotiable — **every LLM prompt and completion**.

> "The LLM I/O storage is the part that makes the system defensible. Three years from now a regulator asks 'why did your system recommend settling this trade?' The answer isn't 'the model decided.' It's: here's the exact prompt we sent, the exact response we got, the deterministic rule that validated it, and the two humans who approved it — with timestamps and a cryptographic proof the record hasn't been altered."

**Append-only enforcement:**
- DB level: `BEGIN IMMEDIATE` to serialise the chain tip; no UPDATE/DELETE grants on the table.
- Storage level: WORM object store (S3 Object Lock, COMPLIANCE mode) — physically immutable for the retention period.

**Verification:** a `/verify` endpoint walks the chain, recomputes each hash, checks each `parent_hash` link, and returns `{valid, broken_at}` — cryptographic proof of integrity, runnable on demand by compliance or a regulator.

**Retention:** 7-year hot/warm/cold tiering. Hot = queryable Postgres index; cold = WORM archive.

## 2.3 How rules + audit + approval interlock

```
1. Workflow created     → audit: WORKFLOW_CREATED (+ LLM I/O)
2. Rules engine runs    → audit: RULES_VIOLATION (if any), risk_score recorded
3. Agent proposes       → audit: proposal stored verbatim
4. Approval gate opens  → audit: APPROVAL_REQUEST
5. Humans vote          → audit: APPROVAL_GRANTED/DENIED (role, comment, ts)
6. Quorum met → commit  → audit: TOOL_CALL with idempotency key + receipt hash
7. Side effect fires    → audit: result + external system confirmation
   Every line above is one link in the hash chain.
```

---

# PART 3 — Financial Failure Modes & Graceful Degradation

> "In finance the question isn't 'will it fail' — it's 'when a component fails, does the system fail *safe* or fail *dangerous*?' The whole design biases toward fail-safe: when in doubt, stop and ask a human; never auto-commit on uncertainty."

## 3.1 The failure catalogue

| Failure | Risk if unhandled | Mitigation (fail-safe) |
|---|---|---|
| **Orchestrator crash mid-workflow** | Lost workflow, inconsistent state | Durable checkpoints → resume; no loss |
| **Commit half-succeeds** (wire sent, ledger post fails) | Money moved, books wrong | **Saga compensation** — reverse the wire, escalate; never leave inconsistent |
| **Duplicate execution** (retry/replay) | Double wire | **Idempotency keys** — exactly-once effect |
| **LLM hallucinates a bad proposal** | Wrong action proposed | Rules engine rejects; human never sees an invalid commit; worst case = wasted proposal |
| **LLM unavailable / slow** | Workflow stalls | **Degrade to manual** — humans still operate without the assist; never block on the LLM |
| **Rules engine stale** (world changed during wait) | Commit on outdated validation | **Re-validate at commit**, not just at propose |
| **Approver unavailable** | Workflow stalls indefinitely | **SLA timer → auto-escalate** up the chain |
| **Sanctioned counterparty slips through** | Regulatory breach | Sanctions screen is a hard gate; on screening-service outage, **fail closed** (block, don't pass) |
| **Audit ledger write fails** | Gap in the record | Write to ledger is part of the transaction; if audit fails, the action fails (audit is not best-effort) |
| **Audit chain tamper/corruption** | Indefensible records | `/verify` detects exact break; runbook for chain repair from WORM archive |
| **Counterparty doesn't respond** | Dispute hangs | `COUNTERPARTY_INPUT_REQUIRED` with deadline → escalation |
| **Event bus down** (SSE/Kafka) | UI goes blind | UI shows "disconnected" banner; reconnect with last-event-id; state is in DB, not the bus — nothing lost |
| **Reference data stale** (LEI, FX rates) | Wrong normalisation | Versioned reference data; staleness threshold; flag low-confidence to human |
| **Network partition between services** | Split-brain | Single source of truth in the durable store; services are stateless and reconcile from it |
| **Poison message** (unparseable event) | Consumer crash loop | Dead-letter queue (DLQ) — quarantine and alert, don't crash the consumer |

## 3.2 The principles behind the table

1. **Fail closed, not open.** On uncertainty (sanctions service down, rules can't validate), block and escalate — never auto-proceed.
2. **Audit is not best-effort.** If you can't record it, you can't do it. The audit write is inside the transaction.
3. **The durable store is the source of truth**, not the event bus or the UI. Lose the bus, lose the UI — lose nothing real.
4. **Every commit is reversible or escalatable.** Saga compensation for partial failures; nothing leaves the system in an inconsistent state silently.
5. **Humans are the ultimate fallback.** Every automated path degrades to a manual path. The LLM going down slows the operator; it never stops them.

## 3.3 The one-liner

> "Graceful degradation in finance means the system gets *slower and more manual* under failure, never *faster and more autonomous*. The failure mode of the AI layer is 'humans do it by hand,' which is exactly the state we're already in today — so it's a safe floor."

---

# PART 4 — Performance: Large Data & Concurrent Requests

## 4.1 The honest scale framing (say this first)

> "The first thing I'd note is that this domain's bottleneck is unusual. It's not millions of requests per second — operational volume is thousands to tens-of-thousands of workflows a day. The real bottleneck is **human approval bandwidth**. So I optimise for two things: operator decision latency, and the system staying responsive while many workflows are live and streaming. Raw throughput matters less than tail latency and correctness under concurrency."

## 4.2 Backend — concurrent requests

| Concern | Approach |
|---|---|
| **Many concurrent workflows** | Stateless orchestrator workers behind a load balancer; state in the durable store. Scale horizontally. |
| **Async I/O** | FastAPI + async DB drivers — a worker isn't blocked waiting on DB/LLM/SWIFT; it handles thousands of concurrent in-flight workflows |
| **The audit chain tip is a serialization point** | `BEGIN IMMEDIATE` serialises writes — this is deliberate. Mitigate contention by **sharding the chain per tenant**, so desks don't contend on one global tip |
| **DB connection limits** | Connection pooling (PgBouncer); bounded pool; backpressure rather than unbounded connections |
| **LLM latency (seconds)** | Never in the hot path of a UI interaction; it runs async inside the workflow, results pushed via SSE when ready |
| **Approval gate lookups** | Cache hot gates in Redis; the gate is small and read often by the workbench |

## 4.3 Backend — large data

| Concern | Approach |
|---|---|
| **50k-row reconciliation files** | Stream-process, don't load into memory; chunked parsing; the agent sees summaries + exceptions, not the whole file |
| **Large LLM prompts/outputs in audit** | Store inline up to a threshold (e.g., 64KB); above that, store by reference to object storage and keep the hash inline |
| **Audit table growth (billions of rows)** | Time-partitioned tables; hot/warm/cold tiering; indexes on the four query patterns (workflow_id, actor, event_type, time range) |
| **Document intelligence on big PDFs** | Async pipeline; extract → confidence-route; never block the workflow on OCR |
| **Query patterns** | Purpose-built indexes; avoid full-text scans on payload — extract queryable fields into columns at write time |

## 4.4 Frontend — large data & concurrency

| Concern | Approach |
|---|---|
| **Task queue with thousands of workflows** | **Virtualised list** (react-window / TanStack Virtual) — render only visible rows |
| **The N+1 gate-fetch trap** | Don't fetch each workflow's gate separately. The list endpoint returns `pending_approvals` count inline; fetch the full gate only on detail open |
| **High-frequency SSE events across 50 live workflows** | **Patch the TanStack Query cache directly** — O(1) per event; never refetch the list on an event |
| **Re-render storms from the live feed** | Per-row subscriptions + `memo`; isolate the volatile cell so a tick doesn't re-render the table (the Morgan Stanley pricing technique) |
| **Heavy diff rendering** | Memoise the computed diff; `useDeferredValue` so a large diff render doesn't block interaction |
| **Slow client / fast server** | Bounded SSE queue server-side, drop stale intermediate frames; `STATE_SNAPSHOT` reconciles. Client coalesces rapid updates with `useTransition` |
| **Initial load of a huge audit trail** | Paginate / windowed fetch; verify chain server-side and send a proof, don't ship the whole chain to verify client-side |

## 4.5 The caching strategy

```
Reference data (LEI, instruments, calendars) → long TTL, rarely changes → CDN/Redis
Approval gates (hot, small, read often)        → Redis, invalidate on vote
Workflow list/detail (server state)            → TanStack Query, stale-while-revalidate,
                                                  invalidated by SSE events (patch, not refetch)
Audit (immutable by definition)                → cache aggressively; it never changes once written
```

## 4.6 The interview soundbite

> "I'd resist over-engineering for throughput we don't have. The wins here are: stateless horizontal scaling with state in a durable store; async everything so slow dependencies like the LLM and SWIFT don't block workers; shard the audit chain per tenant to remove the global write bottleneck; and on the frontend, virtualise the queue, patch the cache instead of refetching, and isolate the volatile streaming cells. That keeps a workbench with fifty live, streaming workflows feeling instant — which is the experience that actually matters for an operator clearing their queue."

---

## QUICK-REFERENCE: ONE LINE PER TOPIC

| Topic | The line |
|---|---|
| **Checkpointing** | "Persist state at each step so a months-long, human-gated workflow survives any crash or restart and resumes exactly — paired with idempotency so resume never double-commits." |
| **Rules engine** | "Deterministic and separate from the LLM — the LLM may be wrong, the rules engine may not. It runs twice: to constrain the proposal and to re-validate at commit." |
| **Audit ledger** | "Append-only, SHA-256 hash-chained, stores LLM I/O verbatim — tamper-evident proof a regulator can verify three years later." |
| **Failure modes** | "Fail closed, not open. Under failure the system gets slower and more manual, never faster and more autonomous." |
| **Performance** | "The bottleneck is human approval bandwidth, not QPS — so optimise operator latency: stateless scaling, async I/O, per-tenant audit shards, virtualised UI, patch-don't-refetch." |
