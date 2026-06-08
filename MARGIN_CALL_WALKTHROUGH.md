# Margin Call — End-to-End Walkthrough (Interview Recall Sheet)

> One concrete case, traced through every box, every ID, every event, every render.
> Use this as the answer to: "Walk me through how this all works, end to end."

---

## THE CAST (the boxes)

```
INGESTION WORKER   →  detects the breach              (always running, no human)
FASTAPI GATEWAY    →  the only door to the internet    (auth, commands, SSE)
SSE BUS            →  fans events to browser tabs      (pub/sub, bounded mailboxes)
LANGGRAPH AGENT    →  proposes, runs tools, pauses      (the brain)
RULES ENGINE       →  deterministic risk checks         (runs TWICE — pre & post approval)
RAG / VECTOR DB    →  retrieves grounding evidence       (CSA terms, with citations)
POSTGRES           →  durable state + LangGraph checkpoint (source of truth)
BROWSER / REACT    →  the operator's workbench            (TanStack Query + Zustand + SSE hook)
```

## THE SCENARIO

> Markets move against counterparty CP_ACME's derivatives position overnight.
> Posted collateral ($40M) no longer covers exposure ($46.2M) → a **$6.2M margin call**
> must be issued. The agent investigates, proposes the call amount, a Risk officer and
> a Margin Ops officer approve (N-of-M), and the system issues the call.

---

## THE FULL DIAGRAM

```
 ┌──────────────┐
 │  EXPOSURE    │  Kafka feed: overnight position revaluation
 │  FEED        │
 └──────┬───────┘
        │ consumed by
        ▼
 ┌──────────────────┐   POST /workflows           ┌─────────────────────────────┐
 │ INGESTION WORKER │ ───────────────────────────►│         FASTAPI             │
 │ (always running, │   { type: margin_call,      │  • mints workflowId         │
 │  no human)       │     exposure, collateral }  │  • inserts row, status NEW  │
 └──────────────────┘                             │  • bus.publish (silent —    │
                                                   │    0 subscribers yet)       │
                                                   └──────────────┬──────────────┘
                                                                  │
        ════════════════ time passes — case sits as NEW ════════ │ ═══════════════
                                                                  │
 ┌──────────────────┐   GET /workflows/wf_42/stream              │
 │   BROWSER (UI)   │ ───────────────────────────────────────────┤
 │  Risk Officer's  │   EventSource → SUBSCRIBE                  ▼
 │  workbench tab   │                                  ┌───────────────────┐
 └─────────┬────────┘ ◄──── STATE_SNAPSHOT ────────────│   FASTAPI /stream  │
           │                                           │  auth → bus.subscribe│
           │           ◄──── live AG-UI events ────────│  → drains mailbox   │
           │                                           └─────────┬─────────┘
           │   POST /investigate {Idempotency-Key}               │
           ├──────────────────────────────────────────►          │
           │   ◄── 202 {runId, started:true} (~20ms) ───         │
           │                                                     │
           │                                          ┌──────────▼──────────┐
           │                                          │      SSE BUS        │
           │                                          │ Map<wfId, Set<sub>> │
           │                                          │ bounded mailboxes   │
           │                                          │ (256, drop-oldest)  │
           │                                          │ + history (replay)  │
           │                                          └──────────┬──────────┘
           │                                                     │ publish()
           │                                          ┌──────────▼──────────┐
           │                                          │   AGENT WORKER      │
           │                                          │  dequeues job,      │
           │                                          │  runAgent(wf, run)  │
           │                                          └──────────┬──────────┘
           │                                                     │ streamEvents()
           │                                          ┌──────────▼──────────┐
           │                                          │   LANGGRAPH GRAPH   │
           │                                          │                     │
           │                                          │  gather_evidence ───┼──► RAG / Vector DB
           │                                          │       │             │     (CSA terms, cited)
           │                                          │       ▼             │
           │                                          │    propose ─────────┼──► LLM, structured output
           │                                          │       │             │     (MarginProposal schema)
           │                                          │       ▼             │
           │                                          │  validate_rules ────┼──► RULES ENGINE (pass #1)
           │                                          │       │             │     pre-approval gate
           │                                          │       ▼             │
           │                                          │  await_approval ────┼─── PAUSE (interruptBefore)
           │                                          │   (checkpointed     │     graph SUSPENDS,
           │                                          │    to Postgres,     │     process exits —
           │                                          │    process exits)   │     nothing polls
           │                                          └─────────────────────┘
           │
           │   ◄──── APPROVAL_REQUEST (gate, proposal, rulesResult, blastRadius) ────
           │
           ▼
   ┌───────────────────┐
   │  5-SECOND VIEW    │   what · why · which-rule · who · blast-radius
   │  status:          │
   │  AWAITING_APPROVAL│
   └─────────┬─────────┘
             │  POST /approve {gateId, role: RISK}     (optimistic update, anti-self-approval)
             ▼
        ┌─────────┐        bus.publish(APPROVAL_GRANTED) ─────┬──► Risk Officer's tab  (sees own sig)
        │ FASTAPI │ ───────────────────────────────────────────┴──► Margin Ops tab     (sees it live too)
        └────┬────┘
             │
             │   (separately, Margin Ops Officer's OWN browser tab, OWN session,
             │    SAME UI codebase) → POST /approve {gateId, role: MARGIN_OPS}
             │
             ▼
      quorumSatisfied(gate) === true  (1 of RISK + 1 of MARGIN_OPS signed)
             │
             ▼
   ┌─────────────────────┐
   │  resumeAgent(wfId)  │   compiled.streamEvents(null, config)  ← null = "continue from checkpoint"
   └──────────┬──────────┘
              │
   ┌──────────▼──────────┐
   │   LANGGRAPH GRAPH   │
   │   (resumed)         │
   │                     │
   │  post_validate ─────┼──► RULES ENGINE (pass #2 — pre-commit, fail-closed,
   │       │             │      revalidates exposure since approval may have taken hours)
   │       ▼             │
   │  issue_call ────────┼──► margin.issueCall(idempotencyKey = deterministic)
   │       │             │      → AUDIT_EVENT (hash-chained)
   │       ▼             │      → WORKFLOW_STATE_CHANGED → CALL_ISSUED
   └─────────────────────┘
              │
              ▼
   ◄──── final SSE events ────  Browser: status → CALL_ISSUED, audit row appended
```

---

## PHASE-BY-PHASE WITH CODE

### Phase 0 — Detection (no human)

```ts
// INGESTION WORKER — always running, consumes the exposure feed
async function onExposureMessage(msg: ExposureUpdate) {
  const required = msg.exposure - msg.collateralPosted;          // 46.2M - 40M = 6.2M
  if (required > msg.thresholdMTA) {
    await fetch("https://gateway/workflows", {
      method: "POST",
      body: JSON.stringify({
        type: "margin_call", counterparty: "CP_ACME", csaId: "CSA_2019_114",
        exposure: 46_200_000, collateral: 40_000_000, callAmount: 6_200_000,
      }),
    });
  }
}
```

### Phase 1 — `workflowId` minted (FastAPI)

```ts
app.post("/workflows", async (req, res) => {
  const workflowId = `wf_${ulid()}`;                             // minted ONCE, threads everywhere
  await db.insertWorkflow({ id: workflowId, ...req.body, status: "NEW" });
  bus.publish(workflowId, { type: "WORKFLOW_STATE_CHANGED", to: "NEW", workflowId });
  // 0 subscribers → silent on the wire, but recorded in history for later replay
  res.json({ workflowId });
});
```

### Phase 2 — Browser subscribes (SSE)

```ts
// Browser
function connect(workflowId: string, onEvent: (e: AGUIEvent) => void) {
  const es = new EventSource(`/workflows/${workflowId}/stream`);
  es.onmessage = (raw) => onEvent(JSON.parse(raw.data) as AGUIEvent);
  return () => es.close();
}
```

```ts
// FastAPI /stream — auth, subscribe, snapshot, drain
app.get("/workflows/:id/stream", async (req, res) => {
  authorizeWorkflowAccess(req.user, req.params.id);
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });

  const lastId = req.headers["last-event-id"] ? Number(req.headers["last-event-id"]) : null;
  const sub = bus.subscribe(req.params.id, lastId);               // mailbox created
  const send = (e: AGUIEvent) =>
    res.write(`id: ${e._id}\nevent: message\ndata: ${JSON.stringify(e)}\n\n`);

  const snapshot = await loadWorkflowState(req.params.id);
  send({ _id: snapshot._id, type: "STATE_SNAPSHOT", state: snapshot, workflowId: req.params.id });

  try {
    while (!res.writableEnded) send(await sub.next());            // blocks on mailbox
  } finally {
    bus.unsubscribe(req.params.id, sub);                          // tab closed → cleanup
  }
});
```

### Phase 3 — "Investigate" click → command IN, agent enqueued

```ts
// Browser — fast command, separate channel from SSE
const investigate = useMutation({
  mutationFn: () => fetch(`/workflows/wf_42/investigate`, {
    method: "POST", headers: { "Idempotency-Key": `idem_${ulid()}` },
  }),
  onMutate: () => qc.setQueryData(["workflow", "wf_42"], (o: any) => ({ ...o, status: "INVESTIGATING" })),
});
```

```ts
// FastAPI — mint runId, enqueue, return in ~20ms (does NOT hold the connection open)
app.post("/workflows/:id/investigate", async (req, res) => {
  const idem = req.headers["idempotency-key"] as string;
  if (await alreadyProcessed(idem)) return res.json(await priorResult(idem));
  const runId = `run_${ulid()}`;
  await db.recordRun(req.params.id, runId);
  agentQueue.enqueue({ workflowId: req.params.id, runId, task: "investigate_margin_call" });
  await markProcessed(idem, { runId });
  res.json({ runId, started: true });
});
```

### Phase 4 — Worker wakes LangGraph; the graph shape

```ts
async function workerLoop() {
  while (true) {
    const job = await agentQueue.dequeue();
    await runAgent(job.workflowId, job.runId, { task: job.task });
  }
}

const graph = new StateGraph<MarginState>({/* channels */})
  .addNode("gather_evidence", gatherEvidenceNode)     // RAG: pull CSA terms
  .addNode("propose", proposeNode)                    // LLM: structured proposal
  .addNode("validate_rules", validateRulesNode)       // RULES PASS #1 (pre-approval)
  .addNode("await_approval", awaitApprovalNode)       // PAUSE (interruptBefore)
  .addNode("post_validate", postValidateNode)         // RULES PASS #2 (pre-commit)
  .addNode("issue_call", issueCallNode);              // commit: issue the call

const compiled = graph.compile({
  checkpointer: new PostgresSaver(/*...*/),
  interruptBefore: ["await_approval"],
});

async function runAgent(workflowId: string, runId: string, inputs: AgentInput) {
  const config = { configurable: { thread_id: workflowId } };     // workflowId = checkpoint thread
  for await (const ev of compiled.streamEvents({ ...inputs, runId }, { ...config, version: "v2" })) {
    const aguiEvent = translate(ev, workflowId, runId);
    if (aguiEvent) bus.publish(workflowId, aguiEvent);
  }
}
```

### Phase 5 — RAG evidence + tool-call rendering

```ts
// AGENT — gather_evidence_node (the RAG step)
async function gatherEvidenceNode(state: MarginState) {
  const toolCallId = `tc_${ulid()}`;
  await emit(state, { type: "TOOL_CALL_START", id: toolCallId, tool: "rag.search_csa", workflowId: state.workflowId });

  const hits = await vectorDB.search({
    query: `margin threshold, MTA, rounding, eligible collateral for ${state.counterparty} ${state.csaId}`,
    filter: { counterparty: state.counterparty, docType: "CSA" }, topK: 5,
  });
  const evidence = hits.map((h) => ({ text: h.chunk, source: h.docId, score: h.score }));  // citation lineage

  await emit(state, { type: "TOOL_CALL_END", id: toolCallId, result: { evidence }, workflowId: state.workflowId });
  return { evidence };
}
```

```tsx
// Browser — 3-phase tool call lifecycle, keyed by toolCallId
{toolCalls.map((tc) => (
  <div key={tc.id} className="tool-call">
    <span>{tc.status === "running" ? "⏳" : "✅"} {tc.tool}</span>
    {tc.status === "done" && (
      <Collapsible label="evidence">
        {tc.result.evidence.map((e: Evidence) => (
          <div key={e.source}><cite>{e.source}</cite> — {e.text} <Score v={e.score} /></div>
        ))}
      </Collapsible>
    )}
  </div>
))}
// ✅ rag.search_csa  [evidence ▾]
//    CSA_2019_114 §3(b) — "Minimum Transfer Amount: USD 1,000,000..."  (0.94)
```

### Phase 6 — Structured proposal (typed, streamed reasoning)

```ts
const MarginProposal = z.object({
  action: z.enum(["issue_call", "hold", "escalate"]),
  callAmount: z.number(), roundedTo: z.number(), collateralType: z.string(),
  confidence: z.number().min(0).max(1), reasoning: z.string(),
});

async function proposeNode(state: MarginState) {
  const llm = model.withStructuredOutput(MarginProposal);          // schema-constrained decoding
  const proposal = await llm.invoke(buildPrompt(state.evidence, state));
  await emit(state, { type: "PROPOSAL_READY", proposal, workflowId: state.workflowId });
  return { proposal };
}
```

### Phase 7 — Rules pass #1 (pre-approval)

```ts
async function validateRulesNode(state: MarginState) {
  const tc = `tc_${ulid()}`;
  await emit(state, { type: "TOOL_CALL_START", id: tc, tool: "rules.validate_proposal", workflowId: state.workflowId });
  const result = rulesEngine.evaluate(state.proposal, {
    context: "pre_approval",
    checks: ["amount_matches_exposure", "rounding_per_csa", "within_mta", "collateral_eligible"],
  });
  await emit(state, { type: "TOOL_CALL_END", id: tc, result, workflowId: state.workflowId });
  if (result.riskScore > 0.9) return { rulesResult: result, route: "escalate" };
  return { rulesResult: result };
}
```

### Phase 8 — `APPROVAL_REQUEST`, agent pauses, 5-second view renders

```ts
async function awaitApprovalNode(state: MarginState) {
  const gateId = `gate_${ulid()}`;
  const gate: Gate = { id: gateId, requiredRoles: ["RISK", "MARGIN_OPS"], quorum: "1_of_each", signatures: [] };
  await emit(state, {
    type: "APPROVAL_REQUEST", gate, proposal: state.proposal, rulesResult: state.rulesResult,
    blastRadius: { amount: 6_200_000, currency: "USD", counterparty: "CP_ACME" },
    workflowId: state.workflowId,
  });
  return { approvalGate: gate };
  // checkpointed to Postgres, process EXITS — could resume in 5 minutes or 5 hours
}
```

```tsx
<FiveSecondView
  what={<ProposalDiff label="Call amount" before={0} after={wf.proposal.callAmount} />}
  why={<ProposalBox advisory reasoning={wf.proposal.reasoning} confidence={wf.proposal.confidence} />}
  rule={<RiskPill score={wf.rulesResult.riskScore} />}
  who={<QuorumBar gate={wf.gate} />}
  blast={<BlastRadius amount={wf.blastRadius.amount} currency="USD" counterparty="CP_ACME" />}
/>
```

### Phase 9 — Two officers sign (same UI, different sessions)

> **Same UI codebase, different browser sessions.** Both subscribe to the same `workflowId`
> via SSE — both tabs receive `APPROVAL_GRANTED` live, regardless of where each officer is.

```ts
// Browser — optimistic signature with rollback
const approve = useMutation({
  mutationFn: () => fetch(`/workflows/wf_42/approve`, {
    method: "POST", headers: { "Idempotency-Key": `appr_${ulid()}` },
    body: JSON.stringify({ gateId: wf.gate.id, role: "RISK" }),    // or "MARGIN_OPS" on the other tab
  }),
  onMutate: () => {
    const snap = qc.getQueryData(["workflow", "wf_42"]);
    qc.setQueryData(["workflow", "wf_42"], (o: any) => ({
      ...o, gate: { ...o.gate, signatures: [...o.gate.signatures, { role: "RISK" }] },
    }));
    return { snap };
  },
  onError: (_e, _v, ctx) => qc.setQueryData(["workflow", "wf_42"], ctx!.snap),
});
```

```ts
// FastAPI
app.post("/workflows/:id/approve", async (req, res) => {
  const { gateId, role } = req.body;
  authorizeRole(req.user, role);
  if (req.user.id === proposalAuthor(req.params.id)) throw new Forbidden("anti-self-approval");

  const gate = await signGate(req.params.id, gateId, { user: req.user.id, role, ts: Date.now() });
  bus.publish(req.params.id, { type: "APPROVAL_GRANTED", gate, workflowId: req.params.id });  // fans to BOTH tabs

  if (quorumSatisfied(gate)) await resumeAgent(req.params.id);
  res.json({ gate });
});

async function resumeAgent(workflowId: string) {
  const config = { configurable: { thread_id: workflowId } };
  for await (const ev of compiled.streamEvents(null, { ...config, version: "v2" })) {  // null = resume
    const aguiEvent = translate(ev, workflowId, currentRunId(workflowId));
    if (aguiEvent) bus.publish(workflowId, aguiEvent);
  }
}
```

### Phase 10 — Rules pass #2 (post-approval, fail-closed)

```ts
async function postValidateNode(state: MarginState) {
  const tc = `tc_${ulid()}`;
  await emit(state, { type: "TOOL_CALL_START", id: tc, tool: "rules.revalidate", workflowId: state.workflowId });
  const fresh = rulesEngine.evaluate(state.proposal, { context: "pre_commit", revalidateExposure: true });
  await emit(state, { type: "TOOL_CALL_END", id: tc, result: fresh, workflowId: state.workflowId });
  if (!fresh.passed) {
    await emit(state, { type: "WORKFLOW_STATE_CHANGED", to: "BLOCKED_POST_APPROVAL", workflowId: state.workflowId });
    throw new FailClosed(fresh.reason);                            // never commit on a failed re-check
  }
  return { finalCheck: fresh };
}
```

### Phase 11 — Commit (idempotent, audited)

```ts
async function issueCallNode(state: MarginState) {
  const tc = `tc_${ulid()}`;
  const idemKey = `margincall_${state.workflowId}_${state.runId}`;  // deterministic — survives crash+resume
  await emit(state, { type: "TOOL_CALL_START", id: tc, tool: "margin.issue_call", workflowId: state.workflowId });
  const receipt = await marginSystem.issueCall(
    { counterparty: state.counterparty, amount: state.proposal.callAmount, csaId: state.csaId },
    { idempotencyKey: idemKey },
  );
  await emit(state, { type: "TOOL_CALL_END", id: tc, result: receipt, workflowId: state.workflowId });
  await emit(state, { type: "AUDIT_EVENT", receipt, hashChainPrev: lastHash(state), workflowId: state.workflowId });
  await emit(state, { type: "WORKFLOW_STATE_CHANGED", to: "CALL_ISSUED", workflowId: state.workflowId });
  return { receipt };
}
```

### Browser — final dispatch

```ts
function dispatch(event: AGUIEvent) {
  switch (event.type) {
    case "STATE_SNAPSHOT":       qc.setQueryData(["workflow", event.workflowId], event.state); break;
    case "TEXT_MESSAGE_CONTENT": setTokens((p) => p + event.delta); break;
    case "TOOL_CALL_START":      addToolCall({ id: event.id, tool: event.tool, status: "running" }); break;
    case "TOOL_CALL_END":        patchToolCall(event.id, { status: "done", result: event.result }); break;
    case "APPROVAL_REQUEST":
      qc.setQueryData(["workflow", event.workflowId], (o: any) => ({
        ...o, status: "AWAITING_APPROVAL", gate: event.gate, proposal: event.proposal,
      }));
      break;
    case "APPROVAL_GRANTED":
      qc.setQueryData(["workflow", event.workflowId], (o: any) => ({ ...o, gate: event.gate })); break;
    case "WORKFLOW_STATE_CHANGED":
      qc.setQueryData(["workflow", event.workflowId], (o: any) => ({ ...o, status: event.to })); break;
    case "AUDIT_EVENT":
      setAuditLog((p) => [...p.slice(-49), event]); break;
    default:
      const _exhaustive: never = event;                            // new event unhandled = compile error
      return _exhaustive;
  }
}
```

---

## EVENT FLOW: internal → AG-UI → render

```
LangGraph internal event          translate()                AG-UI event              React render
────────────────────────────────────────────────────────────────────────────────────────────────────
on_tool_start (rag.search_csa)  →  TOOL_CALL_START      →   {type, id, tool}      →  ⏳ rag.search_csa
on_tool_end   (csa chunks)      →  TOOL_CALL_END        →   {type, id, result}    →  ✅ + evidence ▾
on_chat_model_stream ("The...") →  TEXT_MESSAGE_CONTENT →   {type, delta}         →  streaming reasoning
(node output: proposal)         →  PROPOSAL_READY       →   {type, proposal}      →  structured proposal card
(node: gate defined)            →  APPROVAL_REQUEST     →   {type, gate, blast}   →  5-second decision view
(signature recorded)            →  APPROVAL_GRANTED     →   {type, gate}          →  QuorumBar 1of2 → 2of2
(commit receipt)                →  WORKFLOW_STATE_CHANGED → {type, to}            →  status → CALL_ISSUED
                                →  AUDIT_EVENT          →   {type, receipt}       →  hash-chained audit row
```

---

## THE COMPLETE ID LINEAGE

```
workflowId   "wf_42"                  minted: FastAPI @ creation       → checkpoint thread_id, bus key, DB row
runId        "run_7"                  minted: FastAPI @ /investigate   → one execution
gateId       "gate_x"                 minted: agent @ await_approval   → one approval cycle
toolCallId   "tc_9","tc_10"...        minted: agent, per tool call     → stitches START↔END, keys UI row
_id (seq)    42,43,44...              minted: bus.publish, monotonic   → Last-Event-ID reconnect replay
idemKey      "appr_abc"               minted: browser @ click          → no double-sign on double-click
idemKey      "margincall_wf_42_run_7" minted: agent, deterministic     → exactly-once real-world effect
```

---

## SAME UI OR DIFFERENT? (the question that prompted this doc)

**Same UI codebase. Different browser sessions.**

```
Risk Officer's laptop                          Margin Ops Officer's laptop
   React workbench                                 React workbench
   EventSource(/workflows/wf_42/stream)            EventSource(/workflows/wf_42/stream)
        │                                                │
        └──────────────► SSE BUS ◄───────────────────────┘
                    subscribers.get("wf_42") = {mailboxA, mailboxB}
                              │
                    publish(APPROVAL_GRANTED) → fanned to BOTH
```

What differs is **who's logged in** (separate sessions, separate roles `RISK`/`MARGIN_OPS`),
**where they are** (could be different floors, countries, or hours apart), and
**what the gate requires of them**. What's identical: the UI, the workflow, the `workflowId`
they're both subscribed to. If both happen to look at the same time, each sees the other's
signature land live — nobody signs blind. That live cross-visibility is a deliberate
trust-building feature of the bus's fan-out, not an accident.

---

## THE 60-SECOND VERBAL VERSION (say this when asked "walk me through it")

> "An ingestion worker consuming an exposure feed detects a margin breach and POSTs to
> create a workflow — `workflowId` is minted once and threads through every box. The
> operator opens the case; the browser opens an SSE stream, FastAPI authenticates,
> subscribes them to the bus, and sends a full state snapshot. They click Investigate —
> a quick command that mints a `runId`, enqueues a job, and returns in 20ms while the
> agent runs asynchronously. A worker wakes the LangGraph agent, which RAG-searches the
> CSA terms — that tool call streams to the UI as a running-then-done row with cited
> evidence. The LLM returns a *structured* proposal, not prose; the rules engine
> validates it deterministically — pass one. The agent defines an N-of-M approval gate
> and checkpoints itself to Postgres, then exits — nothing polls for hours. The
> five-second decision view renders what/why/which-rule/who/blast-radius. A Risk officer
> and a Margin Ops officer — same UI, separate sessions — each sign; optimistically
> rendered, anti-self-approval enforced server-side, both tabs updating live through the
> bus. Quorum met, the server resumes the graph from its checkpoint; rules run a *second*
> time against fresh exposure and fail closed if anything drifted; then the call is
> issued with a deterministic idempotency key for exactly-once, and an AUDIT_EVENT is
> hash-chained. Throughout: SSE not WebSocket, three guarantees — no gap, no OOM, no
> blocked producer — and a typed discriminated-union dispatch where a new event the
> frontend forgot to handle is a compile error, not a production incident."
