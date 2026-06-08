# FE Concepts — VP-Level Conversational Depth

> The "they assume you just know this" list. Each section: mechanism, code, and the
> line to say out loud. Pair with CODING_DRILLS.md (the hands-on versions).

---

## 1. SSE vs WebSocket vs Polling

- **Polling** — client asks "anything new?" on a timer. Stateless, trivial, wasteful (most responses empty; up to `interval` stale).
- **SSE** — one long-lived HTTP response that never closes; server writes events as they happen. **One direction: server→client.** Plain HTTP.
- **WebSocket** — HTTP handshake then `Upgrade` to a persistent **bidirectional** TCP socket. Own framing, not HTTP after upgrade.

| | Polling | SSE | WebSocket |
|---|---|---|---|
| Direction | client pull | server → client | bidirectional |
| Transport | HTTP | HTTP (text/event-stream) | TCP after upgrade |
| Reconnect | trivial | automatic + Last-Event-ID | manual (you build it) |
| Proxies/infra | always | works (just HTTP) | can be blocked/buffered |
| Sticky sessions | no | no | often yes |
| Binary | no | no (text) | yes |

> "For pushing agent state — tokens, tool calls, approvals — it's one-directional, so SSE.
> Just HTTP, survives bank proxies, auto-reconnects with Last-Event-ID, no sticky sessions.
> WebSocket only for genuine bidirectional low-latency like live FX pricing. Polling is the
> degraded fallback, not primary."

---

## 2. Three state layers — why not Redux-for-everything

| Layer | Tool | Examples | Property |
|---|---|---|---|
| Server state | TanStack Query | workflow data, queue | shared, goes stale, needs revalidation |
| UI state | Zustand | panel open, selected row, filter | client-only, ephemeral |
| Event state | custom SSE hook | the live stream | transient, drives patches |

> "Redux treats server data as client state — you hand-write fetching, caching, invalidation,
> loading flags. That's hundreds of lines reimplementing what TanStack gives declaratively,
> because server state is shared, goes stale, needs background revalidation. Zustand for
> genuinely client-only ephemera. Discipline: never put server data in Zustand, never put UI
> ephemera in TanStack. Redux isn't wrong — it's the wrong granularity."

Killer pattern — **patch-not-refetch**:
```ts
case "APPROVAL_GRANTED":
  qc.setQueryData(["workflow", id], (old) => ({ ...old, gate: event.gate }));
```

---

## 3. Reconnect / Last-Event-ID / Backpressure

```
server: id: 44 \n data: {...} \n\n
        [drop]
browser: reconnect → header "Last-Event-ID: 44"
server:  replay where _id > 44, OR fresh STATE_SNAPSHOT if too much elapsed
```

- **Snapshot then delta** on every connect → no gap whether joining late or reconnecting.
- **Backpressure**: bounded queue (256), drop OLDEST on full — newest matters most in live state, next snapshot heals gaps. Producer never blocks on a slow consumer.
- **Exponential backoff** on reconnect: 1→2→4→…→30s cap.

---

## 4. React rendering / reconciliation / keys / memo

- **Model**: state change → re-run component → diff virtual tree (reconciliation) → commit only real DOM diffs. Re-render ≠ DOM update.
- **Keys**: stable `key={id}` = "same element, patch it"; `key={index}` breaks on insert/reorder → remount, lost focus/state. Index keys only safe for static append-only lists.
- **memo**: skips re-render on shallow-equal props. **Helps** for expensive children that re-render often with same props. **Hurts** when props change every render anyway (pay comparison for nothing) or inline objects/functions break referential equality. Pair with useCallback/useMemo or it's theatre.

---

## 5. useEffect cleanup / deps / stale closures

```tsx
// STALE CLOSURE BUG — count frozen at 0
useEffect(() => {
  const id = setInterval(() => setCount(count + 1), 1000);
  return () => clearInterval(id);
}, []);

// FIX — functional update never reads the stale capture
useEffect(() => {
  const id = setInterval(() => setCount((c) => c + 1), 1000);
  return () => clearInterval(id);
}, []);
```

> "Cleanup runs before re-run and on unmount. Skip it on a subscription/timer/listener and
> you leak. Stale closure: an effect captures values from the render it ran in; fix by listing
> the dep or using the functional updater. For callbacks I want fresh but don't want to
> resubscribe on, I stash in a ref and update the ref in a separate effect."

---

## 6. TS structural typing / generics / unknown vs any

```ts
// structural: same shape = interchangeable
type A = { id: string }; type B = { id: string };
const b: B = ({ id: "x" } as A);   // fine

// unknown vs any
const x: any = JSON.parse(s); x.foo.bar;       // compiles, explodes at runtime
const y: unknown = JSON.parse(s);
if (isAGUIEvent(y)) y.type;                      // must narrow first — honest top type
```

> "`any` disables the compiler and poisons everything it touches. `unknown` is the honest top
> type: prove what it is before using it. At trust boundaries — parsing SSE, API responses —
> type `unknown`, narrow with a guard. In finance that's compile-time handling vs runtime crash."

---

## 7. Discriminated union + narrowing

```ts
type Proposal =
  | { kind: "settle";   amount: number; counterparty: string }
  | { kind: "hold";     reason: string }
  | { kind: "escalate"; toDesk: string; severity: 1 | 2 | 3 };

function describe(p: Proposal): string {
  switch (p.kind) {
    case "settle":   return `Settle ${p.amount} with ${p.counterparty}`;
    case "hold":     return `Hold: ${p.reason}`;
    case "escalate": return `Escalate to ${p.toDesk} sev ${p.severity}`;
    default: const _: never = p; return _;   // 4th kind → compile error
  }
}
```

---

## 8. Generic hook

```ts
function useResource<T>(key: string, fetcher: (key: string) => Promise<T>) {
  const [data, setData] = useState<T>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetcher(key)
      .then((d) => { if (!cancelled) { setData(d); setError(null); } })
      .catch((e) => { if (!cancelled) setError(e); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [key]);
  return { data, loading, error };
}
```

> "`<T>` makes it reusable and fully typed — `data` is `Workflow | undefined`, no casts at the
> call site since T is inferred from the fetcher. The `cancelled` flag prevents setState after
> unmount/key-change mid-flight."

---

## 9. never / as const / branded types — finance safety trio

```ts
// never — exhaustiveness (see §7)

// as const — freeze to narrowest literal
const roles = ["RISK", "MARGIN_OPS"] as const;
type Role = typeof roles[number];   // "RISK" | "MARGIN_OPS", not string

// branded types — defeat structural typing on purpose
type WorkflowId = string & { readonly __brand: "WorkflowId" };
type RunId      = string & { readonly __brand: "RunId" };
function loadWorkflow(id: WorkflowId) {}
const r = "run_7" as RunId;
loadWorkflow(r);   // ERROR — RunId is not WorkflowId though both are strings
```

> "`never` turns 'forgot a case' into a build failure. `as const` keeps a role list a precise
> union, not widened string. Branded types make a workflowId and runId non-interchangeable
> even though both are strings — the exact silent bug that books against the wrong identifier
> in settlement. In a domain where a wrong string is a wrong trade, the compiler should catch
> ID and currency mix-ups before runtime."

---

## THE THROUGHLINE

> "Runtime side (SSE, three-layer state, reconnect, rendering, effects): decouple rates and
> scopes that don't match, clean up what you start. Type side (unions, generics, unknown,
> never, branding): make illegal states unrepresentable so the compiler catches finance bugs
> before production. VP-level FE in regulated finance is hired for exactly that — fast and
> correct not in tension because architecture and types enforce both."
