/**
 * INTERVIEW TOPIC: when memoization actually matters.
 *
 * The honest answer interviewers want:
 *  - Re-renders are usually cheap; memoize when a *measured* problem exists:
 *    expensive subtrees re-rendering with unchanged props, or hot paths like
 *    a token stream re-rendering 30×/second.
 *  - React.memo only helps if props are referentially stable — an inline
 *    object/lambda prop defeats it, which is why useMemo/useCallback exist:
 *    they serve memoized children, they aren't speedups by themselves.
 *  - Streaming chat case: the transcript appends deltas constantly; memoizing
 *    each completed MessageRow means only the in-flight message re-renders.
 *  - Mention the modern caveat: the React Compiler memoizes automatically,
 *    so hand-written memo is increasingly a legacy/edge-case tool — knowing
 *    *that* is a signal you keep current.
 *
 * Demo: type in the input. The memoized row never re-renders; the unmemoized
 * one re-renders on every keystroke (watch the render counters).
 */
import { memo, useCallback, useRef, useState } from 'react';

function useRenderCount() {
  const count = useRef(0);
  count.current += 1;
  return count.current;
}

interface RowProps {
  label: string;
  onSelect: (label: string) => void;
}

function PlainRow({ label, onSelect }: RowProps) {
  const renders = useRenderCount();
  return (
    <li>
      <button type="button" className="btn" onClick={() => onSelect(label)}>
        {label}
      </button>{' '}
      <em>rendered {renders}×</em>
    </li>
  );
}

// Same component, wrapped: skipped when props are shallow-equal.
const MemoRow = memo(PlainRow);

export function MemoDemo() {
  const [text, setText] = useState('');
  const [selected, setSelected] = useState<string | null>(null);

  // Without useCallback this lambda is a new reference every render,
  // and React.memo on the child would never bail out.
  const onSelect = useCallback((label: string) => setSelected(label), []);

  return (
    <section>
      <h2>Memoization</h2>
      <div className="row">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type here to trigger parent re-renders"
          aria-label="Trigger re-renders"
        />
      </div>
      <ul className="panel">
        <PlainRow label="Not memoized" onSelect={onSelect} />
        <MemoRow label="React.memo + useCallback" onSelect={onSelect} />
      </ul>
      <p className="panel panel--muted">Selected: {selected ?? 'nothing yet'}</p>
    </section>
  );
}
