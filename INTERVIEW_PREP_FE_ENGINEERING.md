# Front-End Engineering Round — Interview Prep
## Agentic Interface Lead, Applied AI — JPMorganChase

> Focus: modern React, TypeScript, streaming UI, framework selection, state management.
> Bring your Morgan Stanley FX (WebSocket pricing), Walmart IoT (real-time dashboards), and Microsoft (Copilot, 11-agent) experience into every answer.

---

## SECTION 1 — React & TypeScript at Staff Level

### 1.1 Concurrent React — when it actually matters

The interview test: do you understand *why* concurrent features exist, not just their names.

> "Concurrent React lets you mark some state updates as non-urgent so they don't block urgent ones. The canonical finance case is exactly what I hit at Morgan Stanley: a live pricing feed updating many times a second, and an operator typing in a filter box. If filtering re-renders synchronously, the typing janks and — worse — the price updates stall behind it. With `useTransition`, the filter update is interruptible; the price stream stays smooth."

```tsx
function WorkflowQueue({ events }: { events: WorkflowEvent[] }) {
  const [query, setQuery] = useState("");
  const [isPending, startTransition] = useTransition();
  const [filter, setFilter] = useState("");

  function onType(value: string) {
    setQuery(value);                    // urgent: keystroke shows instantly
    startTransition(() => setFilter(value)); // non-urgent: heavy filter, interruptible
  }

  const visible = useMemo(
    () => events.filter(e => e.title.includes(filter)),
    [events, filter]
  );
  return <>{isPending && <Spinner/>}{visible.map(renderRow)}</>;
}
```

- `useDeferredValue` — same idea, but for a value you don't own the setter of (props).
- `Suspense` — declarative loading boundaries; pairs with `React.lazy` for code-split panels (the audit viewer doesn't need to load until opened).

**When NOT to use it:** don't wrap genuinely urgent updates (the approve button's own click feedback) in a transition. It would make the UI feel laggy.

### 1.2 Discriminated unions over `any`/`unknown`

> "For AG-UI events the discriminator is `event_type`. A discriminated union gives me compiler-enforced exhaustiveness — if someone adds a new event type, every `switch` that doesn't handle it fails to compile. That's not a style choice in finance; it's how you guarantee a new `SANCTIONS_HIT` event can't be silently dropped by the UI."

```ts
type FinanceEvent =
  | { type: "APPROVAL_REQUEST"; gate_id: string; quorum: number }
  | { type: "APPROVAL_GRANTED"; gate_id: string; signer: string }
  | { type: "SANCTIONS_HIT";    counterparty: string; severity: "BLOCK" | "REVIEW" };

function reduce(e: FinanceEvent) {
  switch (e.type) {
    case "APPROVAL_REQUEST": return /* ... */;
    case "APPROVAL_GRANTED": return /* ... */;
    case "SANCTIONS_HIT":    return /* ... */;
    default: { const _exhaustive: never = e; return _exhaustive; } // compile error if a case is missing
  }
}
```

### 1.3 State architecture — the decision matrix

| Need | Tool | Why |
|---|---|---|
| Server data (workflows, gates, audit) | **TanStack Query** | stale-while-revalidate, cache, optimistic updates, dedup |
| Cross-component UI state (selected workflow, role, connection status) | **Zustand** | tiny, no boilerplate, selectors avoid re-renders |
| A single workflow's lifecycle | **XState** | the workflow IS a state machine; mirrors the server |
| Purely local (a form's open/closed) | **useState** | don't reach for a store |
| Complex local transitions in one component | **useReducer** | predictable, testable |
| Global app store of everything | **Redux** | usually overkill here — I'd avoid it |

> "The mistake I see is reaching for one tool for everything. Server state and UI state have different lifecycles — server state can go stale and needs revalidation; UI state doesn't. Conflating them in Redux means you hand-roll caching that TanStack Query gives you for free."

### 1.4 Performance — what causes re-renders

> "A component re-renders when its state changes, its parent re-renders, or its context value changes. The three fixes are: `memo` to skip re-render when props are referentially equal, `useCallback`/`useMemo` to keep those props stable, and splitting context so a change to one slice doesn't re-render consumers of another. But I apply them *after* profiling with the React DevTools Profiler — premature memoization adds complexity and can even be slower. The rule is: measure the flamegraph, find the component re-rendering 60x/sec, fix that one."

### 1.5 Custom hook for the event stream

```ts
function useWorkflowEvents(workflowId?: string) {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource(`/api/events/stream${workflowId ? `?workflow_id=${workflowId}` : ""}`);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as FinanceEvent;
      // push directly into the cache — no refetch
      if (ev.type === "WORKFLOW_STATE_CHANGED")
        qc.setQueryData(["workflow", ev.workflow_id], (old: any) => ({ ...old, state: ev.state }));
    };
    return () => es.close();
  }, [workflowId, qc]);
}
```

---

## SECTION 2 — Streaming UI Integration (your differentiator)

### 2.1 SSE at the protocol level

> "SSE is just a long-lived HTTP response with `Content-Type: text/event-stream`. The server writes `data: {...}\n\n` frames. The browser's `EventSource` parses them, auto-reconnects on drop, and — this is the part people miss — sends a `Last-Event-ID` header on reconnect if the server set `id:` fields, so you can resume from where you left off. That last-event-id resumption is what makes it correct for a workbench: if the operator's wifi blips, they don't miss the `APPROVAL_GRANTED` that arrived during the gap."

### 2.2 The StreamBuffer — partial chunks

> "Token streaming doesn't arrive on clean JSON boundaries. A chunk might be half an object. So you buffer raw text, split on the `\n\n` delimiter, and only parse complete frames; the trailing partial stays in the buffer for the next chunk."

```ts
class StreamBuffer {
  private buf = "";
  push(chunk: string): FinanceEvent[] {
    this.buf += chunk;
    const frames = this.buf.split("\n\n");
    this.buf = frames.pop() ?? "";          // keep the incomplete tail
    return frames
      .filter(f => f.startsWith("data:"))
      .map(f => JSON.parse(f.slice(5).trim()) as FinanceEvent);
  }
}
```

### 2.3 Progressive tool-call rendering

> "When an agent's tool call streams in, the name arrives first, then args token by token. I render a skeleton card the instant `TOOL_CALL_START` lands — 'Drafting SWIFT MT103…' — and fill the args as they stream. The operator sees progress, not a spinner. On `TOOL_CALL_END` the card flips to its final, interactive state."

### 2.4 Backpressure & reconnection

> "If the server outpaces a slow client, you need a bounded queue and a drop policy. On the backend our SSE bus uses a bounded `asyncio.Queue` and drops on `QueueFull` rather than growing memory unbounded — for a UI feed, dropping a stale intermediate frame is fine; the next `STATE_SNAPSHOT` corrects it. On the client, reconnection is exponential backoff (1s, 2s, 4s, capped) with `Last-Event-ID` resumption."

### 2.5 The full `useSSEStream` hook

```ts
function useSSEStream(url: string, onEvent: (e: FinanceEvent) => void) {
  useEffect(() => {
    let es: EventSource;
    let retry = 1000;
    const connect = () => {
      es = new EventSource(url);
      es.onmessage = (m) => {
        try { onEvent(JSON.parse(m.data)); retry = 1000; } catch {/* heartbeat */}
      };
      es.onerror = () => {
        es.close();
        setTimeout(connect, retry);
        retry = Math.min(retry * 2, 16000);   // exponential backoff, capped
      };
    };
    connect();
    return () => es?.close();
  }, [url, onEvent]);
}
```

### 2.6 Approval card transitions without refresh

> "The card subscribes to events for its workflow. `APPROVAL_REQUEST` flips it from 'pending' to showing the N-of-M gate. Each `APPROVAL_GRANTED` advances the progress bar — `1/2`, `2/2` — by patching the TanStack Query cache directly. No page refresh, no refetch. When quorum is met, `WORKFLOW_STATE_CHANGED` flips it to RESOLVED. The whole thing is event-driven."

---

## SECTION 3 — Framework Selection

### AG-UI
- **What:** an event protocol (RUN/TEXT_MESSAGE/TOOL_CALL/STATE/INTERRUPT/RESUME) decoupling the agent backend from the UI.
- **Good for:** the canonical abstraction for HITL — you extend it with typed finance events and one React tree renders every workflow.
- **Not for:** it's a protocol, not a component kit — you build the rendering.
- **Pick when:** you need a stable contract between a LangGraph/Python backend and a React frontend. **My take:** right default for this role.

### CopilotKit
- **What:** React components + hooks for chat UIs, tool-call rendering, streaming messages.
- **Good for:** dropping a chat affordance into an app fast.
- **Not for:** regulated workbenches where the UI is a queue/diff/gate, not a chat thread; less control over audit and role-gating.
- **Pick when:** chat is genuinely the interaction model, or for rapid prototyping.

### Vercel AI SDK
- **What:** `useChat`/`useCompletion`/`streamText` — clean streaming primitives.
- **Good for:** Next.js apps, fast streaming text UIs.
- **Not for:** opinionated HITL/approval patterns — you'd build those yourself.
- **Pick when:** the product is provider-agnostic streaming chat/completion.

### LangGraph UI integration
> "LangGraph gives you an explicit graph — nodes are states, edges are transitions, and with a checkpointer the whole run is durable and replayable. The integration challenge is that LangGraph doesn't prescribe how to expose state to the UI. I emit an SSE event at *every significant node transition*, not just final output, so the workbench shows live progress and the operator can interrupt early. The frontend mirrors the graph state in an XState machine."

### XState
> "A workflow with OPEN→AWAITING_APPROVAL→RESOLVED is literally a state machine, so model it as one. XState gives you illegal-transition protection on the client, mirroring the server's transition table. I'd *not* use it for trivial component state — that's over-engineering. Use it when the states and guards are real."

---

## SECTION 4 — State Management at Workbench Scale

**The hard question:** *"50 concurrent live workflows, SSE events for any of them, an approval queue, a selected detail panel — how do you manage state?"*

> "Three layers, three tools, clear ownership.
>
> **Server state — TanStack Query.** The workflow list, each gate, the audit trail. Stale-while-revalidate, dedup, cache.
>
> **UI state — Zustand.** Selected workflow id, current role, connection status, the live-events ticker. Small, selector-based so a connection-status change doesn't re-render the queue.
>
> **Event stream — one custom hook** that owns the single SSE connection and fans events into the right cache entries.
>
> The key pattern is: **an SSE event patches the TanStack Query cache directly — it does not trigger a refetch.** `APPROVAL_GRANTED` arrives → I `setQueryData(['workflow', id], patch)`. That's O(1) and instant. Refetching all 50 workflows on every event would melt the backend — that's the N+1 trap."

```ts
// optimistic approve with rollback
const approve = useMutation({
  mutationFn: (v) => api.approve(id, v),
  onMutate: async (v) => {
    await qc.cancelQueries(["workflow", id]);
    const prev = qc.getQueryData(["workflow", id]);
    qc.setQueryData(["workflow", id], patchOptimistic(prev, v)); // UI updates instantly
    return { prev };
  },
  onError: (_e, _v, ctx) => qc.setQueryData(["workflow", id], ctx.prev), // rollback
  onSettled: () => qc.invalidateQueries(["workflow", id]),               // reconcile with truth
});
```

---

## SECTION 5 — Technical Decisions (the "why X over Y")

| Decision | Why |
|---|---|
| **SSE over WebSocket** | Server→client only; stateless; survives HTTP proxies; trivially load-balanced; auto-reconnect + last-event-id built in. WebSocket needs sticky sessions and you don't need bidirectional here. |
| **TanStack Query + Zustand over Redux** | Server state and UI state have different lifecycles. TanStack Query gives caching/revalidation free; Zustand handles the rest with no boilerplate. Redux would mean hand-rolling cache logic. |
| **XState over useReducer for workflows** | Illegal-transition protection and a visualizable machine that mirrors the server's state table. useReducer has no guard concept. |
| **Discriminated unions over Zod in the hot path** | Zod validation per event adds overhead on a high-frequency stream. Validate at the boundary once; use compile-time unions in the render path. |
| **AG-UI over custom protocol** | Don't reinvent; extend a standard. One contract from Python to React. |
| **Patch cache, don't refetch** | Avoids the N+1: 50 workflows × frequent events would be thousands of refetches/min. |

---

## SECTION 6 — Live Coding Cheatsheet

You already have, in this repo's `src/`, working versions of most of these — review them:
- `useSSEStream` → Section 2.5 above
- `WorkflowEventReducer` → discriminated-union switch with `never` exhaustiveness (Section 1.2)
- `ApprovalCard` → see `src/components/ApprovalCard.tsx` (role-aware, anti-self-approval, N-of-M bar — already built)
- `TaskQueue` → optimistic updates via TanStack Query mutation (Section 4)
- `DiffViewer` → render proposed vs current as a two-column structured diff, highlight changed fields
- `FinanceEvent` union → `src/types/domain.ts` (already typed)

**When coding live:** narrate the *decision*, not the syntax. "I'm using a discriminated union here so the compiler enforces every event is handled" beats silently typing.

---

## SECTION 7 — Questions to Ask Them

1. "Is the frontend a single workbench, or per-desk apps sharing a component library?"
2. "Do you use AG-UI today, or a custom event protocol — and is it versioned?"
3. "How do you handle SSE fan-out at your scale — Redis pub/sub, Kafka, or a managed service?"
4. "What's your approach to optimistic updates given that a wrong optimistic state in finance could mislead an operator?"
5. "How do you test streaming UIs — do you mock the SSE stream, replay recorded event logs?"
6. "Is the design system shared firm-wide, and how much latitude does this team have over it?"
7. "How do you handle reconnection correctness — do you use last-event-id resumption or snapshot-on-reconnect?"
8. "What's the React version and are you using the concurrent features in anger yet?"
9. "How do you render the audit trail — is the hash chain verified client-side or server-side?"
10. "Where's the biggest perf pain in the current UI — initial load, the live feed, or the diff rendering?"

---

## SECTION 8 — Anti-Patterns That Fail You at Staff Level

| Don't | Do |
|---|---|
| Refetch on every SSE event | Patch the cache directly |
| `any` for event payloads | Discriminated unions with exhaustive switch |
| Memoize everything preemptively | Profile first, memoize the proven hot path |
| One Redux store for server + UI state | Separate by lifecycle (TanStack Query + Zustand) |
| WebSocket "because real-time" | SSE unless you genuinely need bidirectional |
| Render the agent UI as a chat thread | Workbench: queue + diff + gate; chat is one tab |
| Silently drop unknown events | `never` exhaustiveness so the compiler catches it |
| Block urgent updates behind heavy renders | `useTransition` for non-urgent work |

---

## The line to land

> "Streaming UI for finance isn't about making text appear token by token — it's about keeping an operator's mental model in sync with a durable server-side workflow, correctly, even across reconnects, without ever showing them a stale or misleading state. That's a correctness problem dressed as a UX problem, and it's exactly the kind I worked on with WebSocket pricing at Morgan Stanley."
