# Mastery Reference — HITL Interfaces, Streaming UI, Tool Calls, Structured Outputs

> Everything to master for the Front End + Design rounds. Each topic has:
> **Theory · Differences/Tradeoffs · Architecture · Performance · Scalability · Example.**

---

# TOPIC 1 — HITL INTERFACE DESIGN

## Theory
Human-in-the-loop = the AI proposes, the human commits. The interface's job is to let a human make a high-stakes decision *fast* and *correctly*. The unit of design is **the decision**, not the conversation.

The five things a human needs in <5 seconds:
1. **What** changed (the diff)
2. **Why** (the AI's reasoning — labelled as a proposal)
3. **Which rule** flagged it (deterministic risk)
4. **Who** has signed (N-of-M gate)
5. **What if wrong** (blast radius)

## Differences / Tradeoffs
| Chat model | Workflow model (correct) |
|---|---|
| State = scroll | State = database row / state machine |
| "Approved" = a message | "Approved" = signed gate transition |
| Audit = chat log | Audit = hash chain |
| AI commits on instruction | AI proposes, human commits |
| One person | N-of-M across roles |
| Lost on refresh | Durable across restarts/days |

## Architecture
- Workbench: task queue (risk×SLA) → drill-in (diff+evidence+gate) → event log
- Approval is a **workflow STATE** (AWAITING_APPROVAL), not a button flag
- UI reflects server state machine; never authoritative itself

## Performance
- Queue virtualised (react-window) — 10k workflows, render ~20 visible rows
- Drill-in lazy-loads evidence; queue row is lightweight

## Scalability
- Role-aware affordances driven by data (required_roles), not hardcoded per workflow
- New workflow type = same workbench, different rules module + config

## Example (the decision view)
```tsx
<FiveSecondView
  what={<ProposalDiff before={payload} after={proposal} />}
  why={<ProposalBox advisory reasoning={llm.reasoning} confidence={llm.confidence} />}
  rule={<RiskPill score={0.73} />}
  who={<QuorumBar gate={gate} />}      // 1 of 2, required: COMPLIANCE, RISK
  blast={<BlastRadius amount={6_200_000} currency="USD" />}
/>
```

---

# TOPIC 2 — STREAMING UI

## Theory
LLM output arrives token by token over 2–10s. Streaming renders tokens as they arrive so perceived latency drops and the user sees the agent "thinking." Transport: **SSE** (server→client push) for the workbench; WebSocket only when genuinely bidirectional (FX pricing).

## Differences / Tradeoffs
| | SSE | WebSocket | Polling |
|---|---|---|---|
| Direction | server→client | bidirectional | client pull |
| Reconnect | auto + Last-Event-ID | manual | n/a |
| Proxies/infra | survives (HTTP) | needs upgrade | trivial |
| Sticky sessions | no | often yes | no |
| Use for | agent state push | live pricing | fallback |

## Architecture
```
LangGraph astream_events → translate() → SSE bus → FastAPI EventSource
  → browser EventSource → StreamBuffer → dispatch() → setTokens/patch cache
```

## Performance
- **StreamBuffer**: reassemble partial TCP chunks (split on `\n\n`)
- React batches `setState` — token appends don't each force a sync render
- Coalesce very fast streams to animation frames if needed
- Bounded event log (last 50) — memory doesn't grow

## Scalability
- In-memory SSE bus per node; for multi-node use Redis pub/sub fan-out
- Bounded subscriber queue (256) — drop on full = backpressure, not OOM
- Stateless SSE workers; reconnect lands on any node, replays via Last-Event-ID

## Example — the reconnect-safe hook
```ts
es.onmessage = (raw) => {
  lastEventId.current = raw.lastEventId           // for reconnect replay
  const event = JSON.parse(raw.data) as AGUIEvent
  setLog(prev => [...prev.slice(-49), event])     // bounded
  onEvent(event)                                  // patch cache / drive SM
}
es.onerror = () => { es.close(); setTimeout(connect, backoff); backoff*=2 }
```

---

# TOPIC 3 — TOOL CALL RENDERING

## Theory
The agent calls tools (rules.validate, rag.search, commit). Each call has a lifecycle: requested → running → result. Render each phase so the operator sees *what the agent is doing*, building trust. This is the HITL principle applied to rendering: expose reasoning, don't hide it.

## Differences / Tradeoffs
- **Inline expandable** (result collapsed) vs **always expanded** (noisy)
- **Show all calls** (transparency) vs **show only commits** (less clutter)
  → Default: show all, collapse results. Commits always prominent.

## Architecture
- TOOL_CALL_START → push {tool, status:"running"}
- TOOL_CALL_END → patch same entry to {status:"done", result}
- Keyed by tool-call id so concurrent calls don't collide

## Performance
- Each tool row is independent; a new call appends, doesn't re-render the list
- Collapse heavy result JSON until expanded (don't render 5KB by default)

## Scalability
- Tool classification (read/propose/commit) drives rendering: commit gets the
  confirmation treatment, read tools render quietly

## Example
```tsx
{calls.map(tc => (
  <div key={tc.id}>
    {tc.status === "running" ? "⏳" : "✅"} {tc.tool}
    {tc.status === "done" && <Collapsible label="result"><pre>{json(tc.result)}</pre></Collapsible>}
  </div>
))}
// ✅ rules.validate_settlement  [result ▾]
// ⏳ llm.propose...
```

---

# TOPIC 4 — STRUCTURED OUTPUTS

## Theory
The LLM must return a **typed object**, not free text — so the UI can render it deterministically and the rules engine can validate it. Enforce with a JSON schema / Pydantic model / tool-calling with a defined signature. The proposal is data, never prose-to-be-parsed.

## Differences / Tradeoffs
| Free text | Structured output |
|---|---|
| Parse with regex (fragile) | Typed object, validated |
| UI guesses layout | UI renders by field |
| Can't validate | Rules engine checks it |
| Hallucinated format | Schema-constrained |

## Architecture
```python
class SettlementProposal(BaseModel):
    action: Literal["settle", "hold", "escalate"]
    conditions: list[str]
    confidence: float = Field(ge=0, le=1)
    reasoning: str
llm.with_structured_output(SettlementProposal)   # constrained decoding
```
Backend validates → frontend gets a typed shape → renders per field.

## Performance
- Structured output = no client-side parsing, no retries on malformed text
- Smaller/cleaner payloads than verbose prose

## Scalability
- One schema per workflow type; the UI renders any proposal via a generic
  field-renderer driven by the schema → new type needs no new UI code

## Example — frontend renders the typed proposal
```tsx
function ProposalView({ p }: { p: SettlementProposal }) {
  return (
    <div className="proposal-box">
      <Chip>AI PROPOSAL · advisory</Chip>
      <ConfidenceBadge level={p.confidence} />
      <Field label="Action" value={p.action} />
      <Field label="Conditions" value={p.conditions.join(", ")} />
      <p>{p.reasoning}</p>
    </div>
  )
}
```

---

# TOPIC 5 — STATE MANAGEMENT (3 layers)

## Theory
Three kinds of state, three tools. Server (TanStack), UI (Zustand), Event (SSE hook). The critical pattern is **patch-not-refetch**: SSE event carries the delta, write it straight into the cache.

## Diff / Arch / Perf / Scale
- Diff: server state caches+revalidates; UI state is ephemeral; events stream
- Arch: SSE event → `queryClient.setQueryData` patch → component re-renders
- Perf: zero refetch round-trips on events; SWR avoids spinners on nav
- Scale: patch-not-refetch removes server read load under hot workflows

## Example
```ts
case "APPROVAL_GRANTED":
  queryClient.setQueryData(["workflow", id], old => ({ ...old, approval_gate: event.payload.gate }))
```

---

# TOPIC 6 — CONCURRENT REACT / PERFORMANCE

## Theory
Keep urgent updates (typing) responsive while heavy updates (filtering 10k rows) happen at lower priority. Tools: useTransition, useDeferredValue, Suspense, memo, useSyncExternalStore, react-window.

## Diff / Arch / Perf / Scale
- useTransition: marks an update interruptible (the boundary urgent/deferred)
- useDeferredValue: defers a derived value behind urgent input
- useSyncExternalStore: per-cell subscription → render scope = one cell
- requestAnimationFrame: render rate ≤ refresh rate
- react-window: render only visible rows → 10k rows, ~20 DOM nodes

## Example
```tsx
const deferred = useDeferredValue(filter)
const rows = useMemo(() => all.filter(r => r.match(deferred)), [deferred])
// input updates instantly; list updates when React has spare time
```

---

# TOPIC 7 — TYPE SAFETY

## Theory
Discriminated unions + exhaustive `switch` with `never` default → adding a new event type without handling it is a **compile error**. Types mirror backend Pydantic exactly → one contract, no drift.

## Example
```ts
default: const _: never = e; return assertNever(_)   // build fails if case missing
```

---

# TOPIC 8 — OPTIMISTIC UPDATES + ROLLBACK

## Theory
Patch the cache immediately on user action; if the server rejects, roll back to the snapshot. UI feels instant; server stays source of truth.

## Example
```ts
onMutate: () => { const snap = qc.getQueryData(key); qc.setQueryData(key, optimistic); return { snap } }
onError: (e, v, ctx) => qc.setQueryData(key, ctx.snap)   // rollback
```

---

# TOPIC 9 — FRAMEWORKS

| Tool | Role | Pick when |
|---|---|---|
| AG-UI | event protocol | always first — the wire contract |
| CopilotKit | chat affordance | chat box inside workbench |
| Vercel AI SDK | streaming UI | simpler pure-stream UIs |
| LangGraph (UI side) | graph state exposure | show node progress |
| XState | client state machine | mirror backend workflow |
| TanStack Query | server state | caching + SWR |
| Zustand | UI state | ephemeral client state |
| react-window | virtualisation | long lists |

---

# TOPIC 10 — RESILIENCE / FAILURE (UI side)

## Theory
The network drops, chunks split, tab backgrounds, server restarts. Design for it.

- StreamBuffer → split chunks
- Last-Event-ID → replay missed events on reconnect
- Exponential backoff (1→2→4→8…30s cap) → don't hammer recovering server
- STATE_SNAPSHOT on connect → reconnecting client gets full state, no gap
- Stale indicator → never imply currency you don't have
- Bounded log → backpressure, no OOM

---

# THE MASTERY CHECKLIST

```
[ ] HITL: 5-second decision view, approval-as-state, workbench not chat
[ ] Streaming: SSE vs WS, StreamBuffer, reconnect + Last-Event-ID, backpressure
[ ] Tool calls: 3-phase lifecycle, keyed, collapse results, commit prominent
[ ] Structured outputs: typed proposal, schema-constrained, render per field
[ ] State: 3 layers, patch-not-refetch
[ ] Concurrent React: useTransition, useDeferredValue, useSyncExternalStore, rAF, react-window
[ ] Types: discriminated union + never exhaustiveness, mirror backend
[ ] Optimistic + rollback
[ ] Frameworks: AG-UI first, then rendering lib
[ ] Resilience: reconnect, snapshot+delta, stale state, bounded buffers
```

## The one thesis that connects all ten
> "The frontend's job in agentic finance isn't rendering chat — it's representing a durable state machine honestly: stream the agent's reasoning, render structured proposals as proposals, surface uncertainty as a first-class state, make approval a workflow gate, and stay correct and responsive under live data and flaky networks."
