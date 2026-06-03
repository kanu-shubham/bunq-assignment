# Fluency Guide — LangGraph · AG-UI · Settlement Domain
## Three topics to be conversationally fluent on

---

## TOPIC 1 — LangGraph State Machines & Checkpointers

**What it is:** an agent workflow modelled as a directed graph. Nodes = work (LLM call, tool, rules). Edges = transitions (can be conditional, sometimes LLM-influenced). State = a typed object flowing through, updated by each node.

**Checkpointer = the durability mechanism.** Persists full graph state after EVERY node, keyed by thread_id (one per workflow). Enables four things:

1. Crash recovery — restart loads latest checkpoint, resumes at the next node, context intact.
2. Human-in-the-loop pauses — `interrupt_before=["commit"]` stops and persists, frees the process; resume days later when the human approves (`app.invoke(None, config)`).
3. Time-travel debugging — `app.get_state_history(config)` lists every checkpoint; replay or fork from any.
4. Mid-flight state updates — `app.update_state(config, {...})` writes a human correction before resume.

SQLite checkpointer for dev, Postgres for prod.

**The distinction interviewers love:** LangGraph checkpoints STATE SNAPSHOTS. Temporal EVENT-SOURCES (records events, replays code to rebuild state). Snapshots are simpler — great for agent sub-graphs. Event-sourcing is more robust for months-long workflows. Use Temporal for the durable outer workflow, LangGraph for the agent reasoning sub-graphs.

**Soundbite:** "LangGraph is a state machine where nodes do work and edges route. The checkpointer persists full state after every node, giving me crash recovery, HITL pauses where a workflow waits days on an approval without holding a process, time-travel debugging, and mid-flight state corrections."

---

## TOPIC 2 — AG-UI Event Protocol

**What it is:** a standard protocol streaming events from an agent backend to a UI, decoupling agent from frontend so one UI renders any agent.

**Standard events:**
- Lifecycle: RUN_STARTED, RUN_FINISHED, RUN_ERROR
- Streaming text: TEXT_MESSAGE_START / _CONTENT / _END (typing indicator, accumulate, finalise)
- Tool calls: TOOL_CALL_START / _ARGS / _END (render a tool card progressively: name, then args, then result)
- State: STATE_SNAPSHOT (source of truth) + STATE_DELTA (cheap incremental patch)
- HITL: INTERRUPT / RESUME (agent pauses for human, then continues)

**Why snapshot+delta matters:** deltas are cheap live updates; if a reconnecting client misses deltas, the next snapshot corrects it. This is how you handle reconnection correctly.

**Why EXTEND for finance:** an approval gate isn't just an INTERRUPT — it's N-of-M with roles, quorum, deadline, risk score. So add typed custom events rather than overloading generic ones or reinventing a protocol:
APPROVAL_REQUEST, APPROVAL_GRANTED, APPROVAL_DENIED, OVERRIDE_INVOKED, COUNTERPARTY_INPUT_REQUIRED, REG_FILING_DRAFTED, EVIDENCE_PINNED, SANCTIONS_HIT, SLA_BREACH_WARNING, WORKFLOW_STATE_CHANGED.

**Why extend not reinvent:** (1) inherit streaming/tool/state machinery free; (2) one React tree renders every workflow; (3) AG-UI knowledge transfers, bespoke protocols are tribal. Discriminated union on event_type → adding an event forces every UI switch to handle it or fail to compile.

**Soundbite:** "AG-UI gives lifecycle, streaming text, tool calls, snapshot/delta, interrupt/resume. For finance I keep all of it and add typed events — APPROVAL_REQUEST, OVERRIDE_INVOKED, EVIDENCE_PINNED, SANCTIONS_HIT. Snapshot/delta is also how I handle reconnection: deltas for live updates, snapshot to resync after a gap."

---

## TOPIC 3 — Settlement / Reconciliation / Margin-Call Lifecycle

**Trade lifecycle:** execution → confirmation → clearing → settlement → reconciliation → reporting.

**T+1 / T+2:** T = trade date; settle 1 or 2 business days later. US equities = T+1 (since May 2024). FX spot = T+2. The trade-to-settlement window is where SETTLEMENT RISK lives — that's why operations exists. A SETTLEMENT BREAK = a mismatch on settlement day (cash didn't arrive, amount differs, instruction failed).

**Reconciliation:** comparing records that should match (internal books vs custodian vs counterparty). A RECONCILIATION EXCEPTION/BREAK = they don't. Causes: timing differences, accrued-interest/rounding, fees, genuine errors. Good AI use case — most breaks are benign and repetitive; agent proposes root cause, human confirms.

**Margin calls & disputes:** Margin = collateral covering potential losses. Market moves against you → counterparty issues a MARGIN CALL ("post more collateral"). Terms live in a CSA (Credit Support Annex) under the ISDA Master Agreement. A DISPUTE = parties disagree on collateral owed, usually because they value positions differently (different prices/models/data). Multi-party, runs days-to-weeks → needs durable workflows.

**UTI (Unique Trade Identifier):** globally unique trade ID so both counterparties AND the regulator reference the same trade. Essential for matching the two sides' regulatory reports.

**EMIR:** European Market Infrastructure Regulation — report derivatives to a trade repository (e.g., DTCC), clear standardised OTC via CCPs, margin uncleared ones. EMIR Refit = expanded fields (collateral), ISO 20022. Reporting deadline typically T+1. Siblings: MiFID II (EU), Dodd-Frank (US), SFTR (securities financing).

**SWIFT MT103:** standard message for a single cross-border wire payment. ISO 20022 is the modern XML successor. Agent drafts it, rules validate fields, humans approve, then the wire sends.

**The five demo workflows mapped:**
- T+1 Settlement break → Settlement → Ops approves match before ledger post
- Reconciliation exception → Reconciliation → Ops picks root-cause fix
- Margin-call dispute → Collateral mgmt → Risk+Legal approve response to counterparty
- Cross-border settlement → Settlement → Treasury+Compliance+Ops (2-of-3) approve MT103 before wire
- Regulatory filing (EMIR) → Reporting → Compliance+Legal approve before trade-repository submission

**Soundbite:** "The lifecycle is execution → confirmation → clearing → settlement → reconciliation → reporting. T+1 is the settlement window where settlement risk lives. A settlement break is a mismatch on settlement day; a reconciliation exception is books not matching the custodian, usually timing or accruals; a margin-call dispute is two parties valuing collateral differently under their ISDA CSA, running for weeks. UTI is the shared trade identifier; EMIR is the EU regime requiring T+1 derivative reporting. Every one is a propose-validate-approve-commit workflow — exactly what the platform models."
