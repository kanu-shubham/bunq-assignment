# Coding Drills — Rehearse These Cold (TypeScript, narrate while coding)

> The small live-coding tasks a VP-level agentic-FE round actually gives. Practice typing
> each in a plain editor while saying the "narrate" line. Covers ~90% of the coding bar.

---

## 0. The pub/sub bus (publish / subscribe) — backend, TypeScript

The thing every streaming question traces back to. One producer (agent), many consumers (tabs).

```ts
type AGUIEvent = { _id?: number; type: string; [k: string]: unknown };

class Subscriber {
  private queue: AGUIEvent[] = [];
  private waiting: ((e: AGUIEvent) => void) | null = null;
  private readonly MAX = 256;

  push(event: AGUIEvent) {
    if (this.waiting) { this.waiting(event); this.waiting = null; return; }  // hand off to parked reader
    if (this.queue.length >= this.MAX) this.queue.shift();                   // backpressure: drop oldest
    this.queue.push(event);
  }
  next(): Promise<AGUIEvent> {
    const q = this.queue.shift();
    if (q) return Promise.resolve(q);                                        // already have one
    return new Promise((resolve) => { this.waiting = resolve; });            // park until next push
  }
}

class SSEBus {
  private subscribers = new Map<string, Set<Subscriber>>();
  private history = new Map<string, AGUIEvent[]>();
  private seq = 0;

  publish(workflowId: string, event: AGUIEvent) {
    event._id = ++this.seq;                                  // ① stamp wire id (Last-Event-ID)
    const hist = this.history.get(workflowId) ?? [];         // ② bounded history for replay
    hist.push(event); if (hist.length > 512) hist.shift();
    this.history.set(workflowId, hist);
    for (const sub of this.subscribers.get(workflowId) ?? []) sub.push(event);  // ③ fan out, non-blocking
  }

  subscribe(workflowId: string, lastEventId: number | null): Subscriber {
    const sub = new Subscriber();                            // ① new mailbox
    const set = this.subscribers.get(workflowId) ?? new Set();
    set.add(sub); this.subscribers.set(workflowId, set);     // ② register
    if (lastEventId !== null)                                // ③ replay missed on reconnect
      for (const e of this.history.get(workflowId) ?? []) if (e._id! > lastEventId) sub.push(e);
    return sub;
  }

  unsubscribe(workflowId: string, sub: Subscriber) {
    this.subscribers.get(workflowId)?.delete(sub);
  }
}
export const bus = new SSEBus();
```

> "publish stamps an event, records it in bounded history, fans a copy into every subscriber's
> bounded mailbox without ever blocking. subscribe rents one mailbox per tab and replays missed
> history on reconnect. One producer, many consumers, fully decoupled: fan-out, backpressure
> isolation, gap-free reconnect all fall out of that one split. The Subscriber is a single-slot
> async mailbox — push hands off to a parked reader or buffers (256, drop-oldest); next returns
> a queued event or parks its resolver as a doorbell push rings later."

---

## 1. useWorkflowEvents — SSE hook with cleanup

```tsx
function useWorkflowEvents(workflowId: string, onEvent: (e: AGUIEvent) => void) {
  const onEventRef = useRef(onEvent);
  useEffect(() => { onEventRef.current = onEvent; });        // always call latest closure
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let es: EventSource, backoff = 1000, cancelled = false;
    function connect() {
      es = new EventSource(`/workflows/${workflowId}/stream`);
      es.onopen = () => { setConnected(true); backoff = 1000; };
      es.onmessage = (raw) => onEventRef.current(JSON.parse(raw.data) as AGUIEvent);
      es.onerror = () => {
        setConnected(false); es.close();
        if (!cancelled) { setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 30_000); }
      };
    }
    connect();
    return () => { cancelled = true; es.close(); };          // CLEANUP — no leaked connections
  }, [workflowId]);

  return { connected };
}
```

> "onEvent in a ref so re-renders don't tear down the connection. Cleanup closes the
> EventSource — else every visit leaks a connection. Hand-rolled capped backoff so a recovering
> server isn't hammered."

---

## 2. Streaming tool-call list — keyed merge, 3-phase lifecycle

```tsx
type ToolCall = { id: string; tool: string; status: "running" | "done"; result?: unknown };

function useToolCalls() {
  const [calls, setCalls] = useState<ToolCall[]>([]);
  const dispatch = useCallback((event: AGUIEvent) => {
    if (event.type === "TOOL_CALL_START")
      setCalls((prev) => [...prev, { id: event.id as string, tool: event.tool as string, status: "running" }]);
    if (event.type === "TOOL_CALL_END")
      setCalls((prev) => prev.map((c) => c.id === event.id ? { ...c, status: "done", result: event.result } : c));
  }, []);
  return { calls, dispatch };
}

const ToolCallRow = memo(function ToolCallRow({ call }: { call: ToolCall }) {
  return (
    <div>
      {call.status === "running" ? "⏳" : "✅"} {call.tool}
      {call.status === "done" && <Collapsible label="result"><pre>{JSON.stringify(call.result, null, 2)}</pre></Collapsible>}
    </div>
  );
});

function ToolCallList({ calls }: { calls: ToolCall[] }) {
  return <>{calls.map((tc) => <ToolCallRow key={tc.id} call={tc} />)}</>;
}
```

> "Same id stitches START and END into one row. Match by id, never index — concurrent calls'
> END events can arrive out of order. key={id} = patch in place not remount; memo means a new
> call appending doesn't re-render finished rows."

---

## 3. Discriminated union + exhaustive handler

```tsx
type AGUIEvent =
  | { type: "TEXT_MESSAGE_CONTENT"; delta: string }
  | { type: "TOOL_CALL_START"; id: string; tool: string }
  | { type: "TOOL_CALL_END"; id: string; result: unknown }
  | { type: "APPROVAL_REQUEST"; gate: Gate };

function assertNever(x: never): never { throw new Error(`Unhandled: ${JSON.stringify(x)}`); }

function dispatch(event: AGUIEvent): void {
  switch (event.type) {
    case "TEXT_MESSAGE_CONTENT": appendToken(event.delta); return;
    case "TOOL_CALL_START":      addToolCall(event.id, event.tool); return;
    case "TOOL_CALL_END":        patchToolCall(event.id, event.result); return;
    case "APPROVAL_REQUEST":     showGate(event.gate); return;
    default:                     return assertNever(event);   // new variant → compile error
  }
}
```

---

## 4. 10k-row list — virtualization + deferred filter

```tsx
import { FixedSizeList } from "react-window";

function BigBlotter({ rows }: { rows: Row[] }) {
  const [filter, setFilter] = useState("");
  const deferred = useDeferredValue(filter);
  const filtered = useMemo(() => rows.filter((r) => r.symbol.includes(deferred)), [rows, deferred]);
  const isStale = filter !== deferred;

  return (
    <>
      <input value={filter} onChange={(e) => setFilter(e.target.value)} />
      {isStale && <span className="stale">updating…</span>}
      <FixedSizeList height={600} itemCount={filtered.length} itemSize={32} width="100%">
        {({ index, style }) => <Row key={filtered[index].id} style={style} data={filtered[index]} />}
      </FixedSizeList>
    </>
  );
}
```

> "Two problems two tools: react-window mounts only ~20 visible rows of 10k; useDeferredValue
> keeps typing instant while filtering runs at lower priority. useTransition if I owned the
> setter; useDeferredValue when deriving from a value I don't."

---

## 5. Optimistic approve with rollback

```tsx
function useApprove(workflowId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (role: Role) =>
      fetch(`/workflows/${workflowId}/approve`, {
        method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ role }),
      }).then((r) => { if (!r.ok) throw new Error("rejected"); return r.json(); }),
    onMutate: async (role) => {
      await qc.cancelQueries({ queryKey: ["workflow", workflowId] });
      const snapshot = qc.getQueryData(["workflow", workflowId]);          // save for rollback
      qc.setQueryData(["workflow", workflowId], (old: Workflow) => ({
        ...old, gate: { ...old.gate, signatures: [...old.gate.signatures, { role, pending: true }] },
      }));
      return { snapshot };
    },
    onError: (_e, _r, ctx) => qc.setQueryData(["workflow", workflowId], ctx!.snapshot),  // rollback
    onSettled: () => qc.invalidateQueries({ queryKey: ["workflow", workflowId] }),       // reconcile
  });
}
```

> "onMutate snapshots before patching, then patches instantly. onError restores the exact
> snapshot if the server rejects (anti-self-approval). onSettled reconciles with truth. Instant
> feel, server stays source of truth."

---

## 6. rAF coalescing for high-frequency ticks

```tsx
function usePriceStream(symbol: string) {
  const [price, setPrice] = useState<number | null>(null);
  const latest = useRef<number | null>(null);
  const scheduled = useRef(false);

  const onTick = useCallback((p: number) => {
    latest.current = p;                                   // overwrite — only newest matters
    if (!scheduled.current) {
      scheduled.current = true;
      requestAnimationFrame(() => { setPrice(latest.current); scheduled.current = false; });
    }
  }, []);

  useEffect(() => priceFeed.subscribe(symbol, onTick), [symbol, onTick]);
  return price;
}
```

> "5 ticks in one 16ms frame = the monitor redraws once; those renders are invisible work.
> Buffer latest in a ref, one rAF flush per frame. Coalesces display only — a chart would read
> the raw stream separately."

---

## 7. useDebounce and useThrottle

```tsx
// DEBOUNCE — act once after activity goes quiet
function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);                        // cancel stale timer — KEY line
  }, [value, delayMs]);
  return debounced;
}

// THROTTLE — act at most once per interval
function useThrottle<T>(value: T, intervalMs: number): T {
  const [throttled, setThrottled] = useState(value);
  const lastRan = useRef(Date.now());
  useEffect(() => {
    const remaining = intervalMs - (Date.now() - lastRan.current);
    if (remaining <= 0) { lastRan.current = Date.now(); setThrottled(value); }
    else {
      const id = setTimeout(() => { lastRan.current = Date.now(); setThrottled(value); }, remaining);
      return () => clearTimeout(id);
    }
  }, [value, intervalMs]);
  return throttled;
}
```

> "Debounce: wait for quiet then act once — search-as-you-type, only the final query matters.
> Throttle: act at most once per interval no matter how busy — scroll/resize, steady recent
> updates. Debounce restarts its timer and cancels the stale one; throttle checks elapsed time.
> Both return cleanup or you accumulate timers firing stale callbacks against unmounted state."

---

## THE META-POINT

> "All seven solve one problem: decoupling something at an unbounded network-driven rate from
> something that must stay bounded and responsive — render rate, API rate, re-render scope,
> memory. SSE backoff, tool-call keying, exhaustive types, virtualization, optimistic rollback,
> rAF coalescing, debounce/throttle — seven instances of one senior instinct: find the rate
> mismatch, insert the right primitive at that exact seam."
