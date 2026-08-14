# Pair-programming round — most-asked questions

Prep notes for the live coding session that follows this assignment. Four sections:

1. **[Extensions to *this* codebase](#1-extensions-to-this-codebase)** — by far the most likely format. bunq-style pairing rounds open your own submission and ask you to grow it.
2. **[Classic frontend live-coding questions](#2-classic-frontend-live-coding-questions)** — the standard vocabulary set: debounce, event emitter, concurrency pool, typeahead…
3. **[Questions they ask *about* your code](#3-questions-they-ask-about-your-code)** — verbal, and where most candidates actually lose points.
4. **[Interviewing for Engineering Lead](#4-interviewing-for-engineering-lead)** — what the same round grades differently when the title has "lead" in it. **Read this first if that's the role.**

Ground rule for the whole session: **narrate, then type**. State the approach in one sentence, get a nod, then write. Silence reads as being stuck even when you're not.

---

## 1. Extensions to *this* codebase

The widget is a reducer-driven FSM (`src/features/feedback/state/feedbackMachine.ts`) with a DI seam for the network call. Nearly every extension they can ask for is either *a new state*, *a new action*, or *a new side effect*. Know which one you're being handed before you touch the keyboard.

### 1.1 "Add a new rating tier / a new step to the flow"

The exhaustiveness check does the work for you — say that out loud, it lands well:

```ts
// 1. widen the union
export type Action = /* … */ | { type: 'RETRY' };

// 2. the reducer's `default` branch (`const _exhaustive: never = action`)
//    now fails to compile until a case exists. The compiler is the checklist.
case 'RETRY':
  if (state.status !== STATUS.NEGATIVE_FORM) return state;
  return { ...state, status: STATUS.SUBMITTING, error: null };
```

Then a transition test in `feedbackMachine.test.ts` before touching any component. Reducer first, UI second — that ordering is the point of the architecture and interviewers notice when you honour it.

### 1.2 "Make the failed submit retry automatically (3 attempts, backoff)"

Where does it go? Not the reducer (must stay pure), not the component (untestable). It goes in the service layer, or as a wrapper around the injected `submitFeedback`:

```ts
export async function withRetry<T>(
  fn: () => Promise<T>,
  { attempts = 3, baseMs = 300 } = {},
): Promise<T> {
  let lastErr: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (err instanceof FeedbackValidationError) throw err;  // don't retry 4xx
      if (i < attempts - 1) {
        const delay = baseMs * 2 ** i + Math.random() * 100;  // jitter
        await new Promise((r) => setTimeout(r, delay));
      }
    }
  }
  throw lastErr;
}
```

Two things to say unprompted: **don't retry validation/4xx errors**, and **jitter** so N clients don't sync up. Test it with fake timers.

### 1.3 "Persist a draft comment so it survives a reload"

The trap is calling `localStorage` during render, and the second trap is assuming it exists (Safari private mode throws on write).

```ts
function useDraft(key: string, value: string) {
  useEffect(() => {
    try { window.localStorage.setItem(key, value); } catch { /* quota / private mode */ }
  }, [key, value]);
}
```

Mention debouncing the write, and clearing the key on `SUBMIT_SUCCESS`.

### 1.4 "Don't show the widget again for 30 days after a submit"

Tests the same instinct: a *policy* is not UI state. Keep it out of the machine — a `shouldPrompt(now)` predicate reading a timestamp, injected like `submitFeedback` is. Then say: "in production this belongs server-side, since localStorage is per-device."

### 1.5 "Cancel the in-flight request when the modal closes"

Classic React lifecycle question. `AbortController` in the service, an `AbortSignal` threaded through, and the abort fired from the effect cleanup:

```ts
useEffect(() => {
  const ac = new AbortController();
  return () => ac.abort();
}, []);
```

Follow-up they always ask: *what happens if the promise resolves after unmount?* Answer: the `dispatch` is a no-op on an unmounted component in React 18, but the request itself still costs the user bandwidth — abort is about the network, not the warning.

### 1.6 "Add infinite scroll to a list of past feedback"

Given the base project (`intersection-observer`), expect this one. The whole answer is: sentinel `<div>` at the bottom, observed; when it intersects and we're not already loading, fetch the next page.

```ts
useEffect(() => {
  const el = sentinelRef.current;
  if (!el || !hasMore) return;
  const io = new IntersectionObserver(
    ([entry]) => { if (entry.isIntersecting) loadMore(); },
    { rootMargin: '200px' },   // prefetch before it's visible
  );
  io.observe(el);
  return () => io.disconnect();
}, [hasMore, loadMore]);
```

Points to volunteer: `rootMargin` for prefetch, a `loadingRef` guard so one scroll doesn't fire three fetches, and *stable* keys (never the array index) because prepending shifts every row. If they push further: virtualisation, and why `IntersectionObserver` beats a scroll listener (off main thread, no layout thrash from `getBoundingClientRect`).

### 1.7 "Make the toast dismissible / queue multiple toasts"

Turns single state into a queue. `ThankYouToast` + `useAutoDismiss` become a reducer over `Toast[]`, and each toast needs its own timer keyed by id. Watch the stale-closure trap when clearing timers.

### 1.8 "Write a test for X"

They may just hand you the failing behaviour. Reach for the DI seam, not module mocking:

```tsx
const submit = jest.fn().mockRejectedValueOnce(new Error('nope'));
render(<FeedbackWidget open submitFeedback={submit} />);
// … assert the error surfaces and the form is editable again
```

Query by role and accessible name (`getByRole('button', { name: /send/i })`), never by class. Assert `await findBy…` rather than sprinkling `waitFor`.

### 1.9 "The comment box should support @mentions / a character counter"

Controlled input, `maxLength` mirrored from `MAX_COMMENT_LENGTH` in the service (single source of truth — export the constant, don't retype `2000`), counter as `aria-live="polite"` only near the limit so screen readers aren't spammed on every keystroke.

### 1.10 "Make it work without JS animations / on slow networks"

Point at the existing `prefers-reduced-motion` handling and the pending live region. Then: optimistic UI vs. pending state, and why this flow deliberately shows `SUBMITTING` instead of lying to the user about a network call that can fail.

---

## 2. Classic frontend live-coding questions

Ordered roughly by how often they come up. Each is ~5–10 minutes of typing.

### 2.1 Debounce

```ts
function debounce<A extends unknown[]>(fn: (...a: A) => void, wait = 300) {
  let t: ReturnType<typeof setTimeout> | undefined;
  const debounced = (...args: A) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
  // Object.assign, not `debounced.cancel = …` — TS won't let you bolt a
  // property onto an already-typed arrow function.
  return Object.assign(debounced, {
    cancel: () => { if (t) clearTimeout(t); t = undefined; },
  });
}
```

Follow-ups, in the order they arrive: add `cancel` (above), add leading-edge, preserve `this` (use a `function` expression and `fn.apply(this, args)`), return the last result.

### 2.2 Throttle

```ts
function throttle<A extends unknown[]>(fn: (...a: A) => void, wait = 300) {
  let last = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  return (...args: A) => {
    const now = Date.now();
    const remaining = wait - (now - last);
    if (remaining <= 0) { last = now; fn(...args); }
    else if (!timer) {                       // trailing call
      timer = setTimeout(() => { last = Date.now(); timer = undefined; fn(...args); }, remaining);
    }
  };
}
```

Be ready to say the difference in one line: *debounce fires once the noise stops; throttle fires at most once per interval.* Search box → debounce. Scroll/resize handler → throttle.

### 2.3 `useDebouncedValue` (the React version they actually want)

```ts
function useDebouncedValue<T>(value: T, delay = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);   // ← the whole question is this line
  }, [value, delay]);
  return v;
}
```

### 2.4 Typeahead / autocomplete

The composite question — debounce + race conditions + a11y. The race is the part they're grading:

```ts
useEffect(() => {
  let cancelled = false;
  const ac = new AbortController();
  fetchResults(query, { signal: ac.signal })
    .then((r) => { if (!cancelled) setResults(r); })
    .catch((e) => { if (e.name !== 'AbortError') setError(e); });
  return () => { cancelled = true; ac.abort(); };
}, [query]);
```

Say the failure it prevents: *response for "ab" arriving after "abc" and overwriting it.* Then a11y: `role="combobox"`, `aria-expanded`, `aria-activedescendant`, arrow keys move a highlighted index without moving DOM focus.

### 2.5 Limit promise concurrency (pool of N)

```ts
async function pool<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;                       // single-threaded, so no lock needed
      results[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}
```

The insight to state: *N workers pulling from a shared cursor*, not chunking into batches of N — batching stalls on the slowest item in each batch.

### 2.6 Implement `Promise.all` / `allSettled` / `any`

```ts
function all<T>(promises: Promise<T>[]): Promise<T[]> {
  return new Promise((resolve, reject) => {
    const out = new Array<T>(promises.length);
    let done = 0;
    if (promises.length === 0) return resolve(out);        // the edge case they check
    promises.forEach((p, i) => {
      Promise.resolve(p).then((v) => {                     // Promise.resolve: handles non-promises
        out[i] = v;
        if (++done === promises.length) resolve(out);
      }, reject);
    });
  });
}
```

Three traps, all deliberate: empty array, index-keyed results (not push order), non-promise values.

### 2.7 Event emitter

```ts
class Emitter<E extends Record<string, unknown[]>> {
  private map = new Map<keyof E, Set<(...a: never[]) => void>>();
  on<K extends keyof E>(k: K, fn: (...a: E[K]) => void) {
    if (!this.map.has(k)) this.map.set(k, new Set());
    this.map.get(k)!.add(fn as never);
    return () => this.off(k, fn);                    // unsubscribe handle
  }
  off<K extends keyof E>(k: K, fn: (...a: E[K]) => void) { this.map.get(k)?.delete(fn as never); }
  emit<K extends keyof E>(k: K, ...args: E[K]) {
    [...(this.map.get(k) ?? [])].forEach((fn) => (fn as (...a: E[K]) => void)(...args));
  }
}
```

Copy the set before iterating — a handler that unsubscribes itself mid-emit otherwise mutates the collection you're looping over.

### 2.8 Deep clone

```ts
function deepClone<T>(value: T, seen = new WeakMap()): T {
  if (value === null || typeof value !== 'object') return value;
  if (seen.has(value)) return seen.get(value);        // cycles
  const out: any = Array.isArray(value) ? [] : value instanceof Date ? new Date(value)
    : value instanceof Map ? new Map() : value instanceof Set ? new Set() : {};
  seen.set(value, out);
  if (value instanceof Map) value.forEach((v, k) => out.set(k, deepClone(v, seen)));
  else if (value instanceof Set) value.forEach((v) => out.add(deepClone(v, seen)));
  else if (!(value instanceof Date)) {
    for (const k of Reflect.ownKeys(value)) out[k] = deepClone((value as any)[k], seen);
  }
  return out;
}
```

Mention `structuredClone` exists, then explain why they'd still ask you to write it.

### 2.9 Memoize / LRU cache

```ts
class LRU<K, V> {
  private map = new Map<K, V>();
  constructor(private limit = 100) {}
  get(k: K): V | undefined {
    if (!this.map.has(k)) return undefined;
    const v = this.map.get(k)!;
    this.map.delete(k); this.map.set(k, v);           // Map preserves insertion order
    return v;
  }
  set(k: K, v: V) {
    if (this.map.has(k)) this.map.delete(k);
    else if (this.map.size >= this.limit) this.map.delete(this.map.keys().next().value);
    this.map.set(k, v);
  }
}
```

The trick worth naming: JS `Map` is insertion-ordered, so delete+reinsert = "move to most-recent", and `keys().next().value` is the LRU victim. O(1) throughout, no linked list needed.

### 2.10 `useFetch` / data-fetching hook

Reducer with `{ status: 'idle' | 'loading' | 'success' | 'error' }` — never four independent booleans, which is the answer they're fishing for. Plus abort on unmount/param change, and a `key` so a changed URL resets state rather than showing the previous result.

### 2.11 Star rating component

Small but a11y-loaded, and directly adjacent to this assignment. Use a radiogroup of real inputs (visually hidden) so keyboard and screen readers work for free; hover state is CSS-only via sibling selectors. If you reach for `<div onClick>`, expect to be asked how a keyboard user rates 4 stars.

### 2.12 Virtualised list

`startIndex = floor(scrollTop / rowHeight)`, render `visibleCount + overscan` rows, absolutely position them inside a spacer div of height `total * rowHeight`. State the limitation before they do: fixed row height; variable heights need measurement + a prefix-sum index.

### 2.13 Odds and ends they still ask

`flatten(arr, depth)`, `groupBy`, `curry`, `Array.prototype.map` polyfill, `Function.prototype.bind` polyfill, `once`, deep `isEqual`, `chunk`, an async `sleep`, and "traverse the DOM and count nodes matching X". Each is 3–5 minutes; the grading is on edge cases and naming, not cleverness.

---

## 3. Questions they ask *about* your code

Verbal, mid-session, while you're typing. Have crisp answers ready — these decide the round more often than the code does.

| Question | The answer that lands |
|---|---|
| "Why a reducer instead of `useState`?" | Six states with illegal transitions between them. A reducer makes the transition table explicit and testable without rendering anything; `useState` scatters it across handlers. |
| "Why is `submitFeedback` a prop?" | Dependency injection. Tests pass a `jest.fn()` — no module mocking, no network stubbing, and the demo app injects a fake so the flow runs without a backend. |
| "What does the `never` in the default branch do?" | Exhaustiveness check. Add an `Action` variant without a case and it's a compile error, not a silent no-op at runtime. |
| "How do you know the modal is accessible?" | Focus trap + restore, `aria-modal` with a labelled title, ESC and backdrop dismiss, live region for the pending state, `prefers-reduced-motion`. Then: I'd still test with a real screen reader — automated checks catch maybe a third of issues. |
| "What would you do differently with more time?" | Have two real answers ready. E.g. server-side dedupe of prompts (localStorage is per-device), and an E2E test over the happy path — jsdom doesn't prove focus behaves in a real browser. |
| "How would you scale this to 20 widgets?" | Extract the machine + modal primitives; keep each widget's states local. Don't reach for global state until two features genuinely share data. |
| "Where would this break in production?" | Network flakiness (hence retry/abort), double-submit on slow connections, and the fact that a failed submit currently loses nothing but *feels* lost without the draft persistence. |
| "Trade-off you deliberately made?" | Name one honestly. Controlled `open` prop means the parent owns visibility — simpler contract, but the widget can't self-dismiss without cooperation. |

### Behavioural rules for the round

- **Ask before assuming.** "Should a failed submit keep the comment?" costs 5 seconds and prevents a wrong implementation.
- **Type the test first** when the ask is behavioural. It's the fastest way to show the reducer-first discipline the codebase already advertises.
- **Say what you'd skip.** "I'd normally add an integration test here; want me to, or keep moving?" — shows judgement about scope, which is the actual signal.
- **When stuck, externalise.** Say what you expected, what you got, and the next thing you'd check. A candidate debugging out loud reads as a good colleague; a candidate frozen reads as neither.
- **Don't fight the tooling.** If a test setup misbehaves, timebox it out loud and move on to what you were asked to demonstrate.

---

## 4. Interviewing for Engineering Lead

Sections 1–3 still apply — a lead who can't write the reducer loses the room. But the *grading* moves. At IC level they ask "can you build it?"; at lead level they ask "what's it like when this person is the most senior engineer on the call?" Those are different rounds wearing the same clothes.

**The single biggest failure mode is taking the keyboard.** A strong IC solves the problem in front of them. A strong lead solves it *through* the other person and still lands it on time. If the interviewer starts typing, let them type — even when you'd be faster.

### 4.1 What the format usually becomes

| Format | What it's really testing |
|---|---|
| "Here's a PR from a teammate — review it out loud." | Prioritisation. Do you lead with the race condition or with naming nits? |
| "Extend the widget, but explain it as if I'm two years in." | Whether you can teach without condescending, and whether you actually understand it or just wrote it. |
| "Sketch how you'd structure this if four teams used it." | Design in code: module boundaries, contracts, versioning — before implementation. |
| "This test is failing and nobody knows why." | Debugging an unfamiliar codebase calmly, out loud, with a hypothesis each step. |
| "We have six months of debt here. What do you do Monday?" | Sequencing and appetite. Not the perfect end state — the *first* move. |

### 4.2 The code-review exercise — order matters

Say your ordering out loud before you start; it *is* the answer:

1. **Correctness and security** — race conditions, auth, data loss, unhandled rejections. Blocking.
2. **Contracts** — public API shape, breaking changes, error semantics. Expensive to change later, so cheap to fix now. Usually blocking.
3. **Tests** — does the test actually fail if the behaviour breaks? Assertion-free tests are worse than none.
4. **Readability and structure** — naming, module boundaries. Suggestions.
5. **Style** — should be automated. If you're spending review time here, the finding is *"we need a linter rule"*, not thirty comments.

Label each finding **blocking / suggestion / nit**, and say the *why* with the *what*. Two lines that consistently impress:

> "This is a nit and I'd merge without it — but if you're already touching the file…"

> "I might be wrong about this one. What happens if the response for the previous query lands after this one?"

The second is the lead move: ask a question that makes them find the bug, rather than announcing it. And when you have twelve findings, say "the first three are blocking, the rest can be follow-ups" — an unranked wall of comments is how leads stall teams.

### 4.3 Design-in-code prompts, tailored to this repo

- **"Four teams need this widget."** Extract the machine + modal primitives into a package; keep each product's *states* local. Public surface is the props contract and the payload type — version those, not internals. Say what you'd deliberately *not* share: copy, styling tokens, and analytics naming.
- **"Product wants to change the flow without a deploy."** Now the FSM is config, and the trade-off is sharp: the `never` exhaustiveness guarantee dies the moment transitions come from JSON. State that cost explicitly, then propose the middle — configurable *copy and thresholds*, hardcoded *transitions*.
- **"Add analytics."** An injected `track` seam, exactly like `submitFeedback`. Vendor stays out of the machine. Event names are a contract with the data team — agree them before writing them.
- **"How does this roll out?"** Flag → internal → 5% → 50% → 100%, with a kill switch and one metric that would make you roll back. Leads who name the rollback trigger unprompted are rare.

### 4.4 Verbal questions aimed at leads

| Question | The answer that lands |
|---|---|
| "How do you set frontend standards across a team?" | Automate what's automatable (lint, types, CI) so review is about design, not commas. Write down only the decisions that were actually contested. Standards nobody can cite aren't standards. |
| "You disagree with a senior engineer's architecture. Then what?" | Disagree in the design phase, in writing, with the trade-off named. If I don't win it and it's reversible, I commit fully and set a checkpoint. Irreversible calls get escalated, not relitigated in the PR. |
| "How do you split a large feature across three engineers?" | By seam, not by layer — vertical slices behind a flag, each shippable. Splitting into "you do CSS, you do state" creates three-way blocking and no owner. |
| "What's your bar for tests?" | Test behaviour at the boundary the user cares about; don't test the reducer's internals *and* the DOM for the same rule. Coverage as a smell, never a target. |
| "How do you pay down debt without stopping delivery?" | Debt gets fixed in the files we're already touching, plus one explicitly funded chunk per quarter for the things that never get touched. "Freeze features for a refactor" is how you lose the argument permanently. |
| "Someone on your team is underperforming." | Specifics, early, in private, with what "better" looks like and a date. Most cases are a mismatch of expectations or context, not capability — and if I only noticed at review time, that's my failure. |
| "How do you run code review at scale?" | Small PRs, fast turnaround as a team norm (a day-old PR is a stalled engineer), and authors pick reviewers by context not seniority. I review to unblock, not to prove I read it. |
| "Migration — React 17 → 18, say. How?" | Incrementally, behind the compatibility layer, with the riskiest surface (concurrent-rendering side effects here) tested first. Name the rollback story. A big-bang branch that lives three months is the failure mode. |
| "What do you do in your first 30 days?" | Ship something small and real in week one to learn the pipeline; read the last quarter of incidents; talk to whoever complains most about the codebase. Change nothing structural until I can explain why it's the way it is. |

### 4.5 Signals that separate lead from senior in this round

- **Scoping out loud.** "Given 40 minutes I'd do the reducer and one integration test, and stub the rest — agree?" Deciding what *not* to build is the job.
- **Making the call.** When asked to choose, choose, name the trade-off, and say what would change your mind. "It depends" without a decision reads as avoidance.
- **Crediting and correcting.** If they spot something, say so plainly. If they're wrong, disagree kindly and with a reason. Both are being watched.
- **Talking about people at all.** Many candidates answer every lead question in pure technical terms. Mention the engineer who'd maintain this, the reviewer, the on-call — it costs one clause and it's the whole distinction.
- **Knowing when good enough is good enough.** Over-engineering to demonstrate range is the most common lead-level self-inflicted wound. "This is more than we need today; here's the seam if it grows" beats building the seam.
