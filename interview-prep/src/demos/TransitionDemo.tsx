/**
 * INTERVIEW TOPIC: useTransition / concurrent rendering.
 *
 * Talking points:
 *  - Concurrent rendering lets React work on a render in the background and
 *    abandon it if newer input arrives — rendering became interruptible.
 *  - useTransition marks an update as non-urgent. Urgent updates (the
 *    controlled input) commit immediately; the transition (filtering 20k
 *    rows) renders concurrently and can be interrupted by the next keystroke.
 *  - Without the transition, every keystroke renders the heavy list
 *    synchronously and typing visibly stutters — toggle the checkbox to feel
 *    the difference.
 *  - isPending drives the "stale results" affordance instead of a spinner
 *    replacing content.
 *  - Related: useDeferredValue (defer a value rather than wrap a setter) and
 *    why this matters in agent UIs — token streams cause very frequent
 *    updates; keep typing/scrolling urgent, mark derived heavy work as
 *    deferred.
 */
import { useState, useTransition } from 'react';

interface SettlementRow {
  id: number;
  counterparty: string;
  status: 'matched' | 'failed' | 'pending';
}

const COUNTERPARTIES = ['ACME Capital', 'Globex', 'Initech', 'Umbrella', 'Stark Industries', 'Wayne Corp'];
const ALL_ROWS: SettlementRow[] = Array.from({ length: 20000 }, (_, i) => ({
  id: i,
  counterparty: `${COUNTERPARTIES[i % COUNTERPARTIES.length]} #${i}`,
  // Non-null assertion: noUncheckedIndexedAccess can't see that i % 3 is in bounds.
  status: (['matched', 'failed', 'pending'] as const)[i % 3]!,
}));

function Row({ row }: { row: SettlementRow }) {
  // Artificial weight so 20k rows are genuinely expensive, like a real grid.
  const start = performance.now();
  while (performance.now() - start < 0.01) {
    /* burn ~10µs */
  }
  return (
    <li>
      {row.counterparty} — {row.status}
    </li>
  );
}

export function TransitionDemo() {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('');
  const [useConcurrent, setUseConcurrent] = useState(true);
  const [isPending, startTransition] = useTransition();

  const onChange = (value: string) => {
    setQuery(value); // urgent: the input must echo the keystroke now
    if (useConcurrent) {
      startTransition(() => setFilter(value)); // non-urgent: interruptible
    } else {
      setFilter(value); // everything urgent: watch it jank
    }
  };

  const visible = ALL_ROWS.filter((r) =>
    r.counterparty.toLowerCase().includes(filter.toLowerCase()),
  ).slice(0, 300);

  return (
    <section>
      <h2>useTransition</h2>
      <div className="row">
        <input
          value={query}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Filter 20,000 settlement rows…"
          aria-label="Filter rows"
        />
        <label>
          <input
            type="checkbox"
            checked={useConcurrent}
            onChange={(e) => setUseConcurrent(e.target.checked)}
          />{' '}
          use transition
        </label>
      </div>
      <ul className="panel" style={{ opacity: isPending ? 0.5 : 1, maxHeight: 280, overflow: 'auto' }}>
        {visible.map((row) => (
          <Row key={row.id} row={row} />
        ))}
      </ul>
    </section>
  );
}
