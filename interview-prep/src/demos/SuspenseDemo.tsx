/**
 * INTERVIEW TOPIC: Suspense + the React 19 `use()` hook.
 *
 * Talking points:
 *  - Suspense decouples "what to show while data isn't ready" from the
 *    component that needs the data. The child just reads; the boundary
 *    owns the fallback.
 *  - `use(promise)` suspends until the promise settles. Unlike hooks it may
 *    be called conditionally. The promise must be *stable* across renders
 *    (cached outside, or created in a parent/server) — creating it inline in
 *    render would suspend forever in a loop. That caching gotcha is the
 *    classic follow-up question.
 *  - Errors from the promise propagate to the nearest Error Boundary, so
 *    loading and error UI are both declarative.
 *  - Server Components angle: on RSC frameworks the data is awaited on the
 *    server and streamed into the Suspense boundary — same mental model,
 *    no client fetch at all. Client components remain for interactivity.
 */
import { Suspense, use, useState, Component, type ReactNode } from 'react';

function fetchAgentRunSummary(runId: string, fail: boolean): Promise<string> {
  return new Promise((resolve, reject) =>
    setTimeout(
      () =>
        fail
          ? reject(new Error('Failed to load run summary'))
          : resolve(`Run ${runId}: 14 settlements matched, 2 exceptions escalated.`),
      900,
    ),
  );
}

// Module-level cache keeps the promise identity stable across re-renders.
const cache = new Map<string, Promise<string>>();
function getSummary(runId: string, fail: boolean) {
  const key = `${runId}:${fail}`;
  let p = cache.get(key);
  if (!p) {
    p = fetchAgentRunSummary(runId, fail);
    cache.set(key, p);
  }
  return p;
}

function RunSummary({ runId, fail }: { runId: string; fail: boolean }) {
  // Suspends here until resolved; throws to the ErrorBoundary on rejection.
  const summary = use(getSummary(runId, fail));
  return <p className="panel">{summary}</p>;
}

class ErrorBoundary extends Component<
  { fallback: (retry: () => void) => ReactNode; children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    return this.state.error
      ? this.props.fallback(() => this.setState({ error: null }))
      : this.props.children;
  }
}

export function SuspenseDemo() {
  const [runId, setRunId] = useState(1);
  const [fail, setFail] = useState(false);

  return (
    <section>
      <h2>Suspense + use()</h2>
      <div className="row">
        <button type="button" className="btn" onClick={() => setRunId((n) => n + 1)}>
          Load next run
        </button>
        <label>
          <input type="checkbox" checked={fail} onChange={(e) => setFail(e.target.checked)} />{' '}
          simulate failure
        </label>
      </div>
      <ErrorBoundary
        fallback={(retry) => (
          <div className="chat__error" role="alert">
            Could not load the run.{' '}
            <button
              type="button"
              className="btn"
              onClick={() => {
                cache.clear();
                retry();
              }}
            >
              Retry
            </button>
          </div>
        )}
      >
        <Suspense fallback={<p className="panel panel--muted">Loading run summary…</p>}>
          <RunSummary runId={String(runId)} fail={fail} />
        </Suspense>
      </ErrorBoundary>
    </section>
  );
}
