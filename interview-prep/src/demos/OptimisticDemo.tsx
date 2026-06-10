/**
 * INTERVIEW TOPIC: useOptimistic (React 19) + form actions.
 *
 * Talking points:
 *  - useOptimistic layers a temporary, optimistic view over confirmed state
 *    while an async action runs. When the action settles and confirmed state
 *    updates (or the action throws), React discards the optimistic layer —
 *    rollback is automatic, not hand-written.
 *  - It must be used inside a transition/form action; here the <form action>
 *    is one automatically.
 *  - Agent-UI relevance: send a chat message and show it instantly while the
 *    request is in flight (this is what AI SDK's useChat does internally).
 *  - HITL caveat worth volunteering: optimistic UI is for *reversible*,
 *    high-success operations. You never optimistically render a funds
 *    transfer as executed — irreversible actions show their real state.
 */
import { useOptimistic, useState, useRef } from 'react';

interface Note {
  id: string;
  text: string;
  pending?: boolean;
}

// Fake server: ~600ms latency, fails when the text contains "fail".
async function saveNote(text: string): Promise<Note> {
  await new Promise((r) => setTimeout(r, 600));
  if (/fail/i.test(text)) throw new Error('Server rejected the note');
  return { id: crypto.randomUUID(), text };
}

export function OptimisticDemo() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const [optimisticNotes, addOptimisticNote] = useOptimistic(
    notes,
    (current, newText: string) => [
      ...current,
      { id: `optimistic-${Date.now()}`, text: newText, pending: true },
    ],
  );

  async function action(formData: FormData) {
    const text = String(formData.get('text') ?? '').trim();
    if (!text) return;
    formRef.current?.reset();
    setError(null);
    addOptimisticNote(text); // shows instantly, flagged as pending
    try {
      const saved = await saveNote(text);
      setNotes((prev) => [...prev, saved]); // confirmed state replaces the optimistic layer
    } catch (err) {
      // Optimistic entry vanishes automatically; we surface why.
      setError(err instanceof Error ? err.message : 'Save failed');
    }
  }

  return (
    <section>
      <h2>useOptimistic</h2>
      <form ref={formRef} action={action} className="row">
        <input name="text" placeholder='Add a note (type "fail" to test rollback)' aria-label="Note" />
        <button type="submit" className="btn btn--primary">
          Add
        </button>
      </form>
      {error && (
        <p className="chat__error" role="alert">
          {error} — the optimistic entry was rolled back.
        </p>
      )}
      <ul className="panel">
        {optimisticNotes.map((note) => (
          <li key={note.id} style={{ opacity: note.pending ? 0.5 : 1 }}>
            {note.text} {note.pending && <em>(sending…)</em>}
          </li>
        ))}
      </ul>
    </section>
  );
}
