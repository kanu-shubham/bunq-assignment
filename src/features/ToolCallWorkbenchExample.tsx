/**
 * ToolCallWorkbenchExample.tsx
 *
 * A single, self-contained, compile-ready reference showing the FULL wiring:
 *
 *   FastAPI SSE stream
 *     → useWorkflowEvents   (owns transport: EventSource, reconnect, cleanup)
 *       → dispatch          (returned by useToolCalls — owns the tool-call state machine)
 *         → setCalls(...)   (React state update, keyed by tool-call id)
 *           → <ToolCallList> (3-phase lifecycle render: ⏳ running → ✅ done [result ▾])
 *
 * This file demonstrates, in order:
 *   1. The AG-UI event union (discriminated union + exhaustive narrowing)
 *   2. useWorkflowEvents — the SSE transport hook (EventSource, ref-stable callback, cleanup, backoff)
 *   3. useToolCalls — the state-owning hook (keyed merge of START/END into one row)
 *   4. <ToolCallList> / <ToolCallRow> — the 3-phase render, memoized, keyed by id
 *   5. <WorkflowWorkbench> — the component that composes all of the above
 *
 * Nothing here needs a build step beyond a standard CRA/Vite + React 18 + TypeScript setup.
 * Swap the fetch URL for your real gateway endpoint and it runs as-is.
 */

import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
  memo,
} from 'react';

// ─────────────────────────────────────────────────────────────────────────────
// 1. THE AG-UI EVENT UNION
//    Discriminated union — `type` is the discriminant. Every consumer narrows on it.
//    Mirrors the backend's event schema exactly (generated from the same source in
//    a real system — "one contract, no drift").
// ─────────────────────────────────────────────────────────────────────────────

export type Gate = {
  id: string;
  requiredRoles: string[];
  signatures: { role: string; user: string }[];
};

export type AGUIEvent =
  | { _id?: number; type: 'STATE_SNAPSHOT'; state: unknown; workflowId: string }
  | { _id?: number; type: 'TEXT_MESSAGE_CONTENT'; delta: string; workflowId: string }
  | { _id?: number; type: 'TOOL_CALL_START'; id: string; tool: string; workflowId: string }
  | { _id?: number; type: 'TOOL_CALL_END'; id: string; result: unknown; workflowId: string }
  | { _id?: number; type: 'APPROVAL_REQUEST'; gate: Gate; workflowId: string }
  | { _id?: number; type: 'APPROVAL_GRANTED'; gate: Gate; workflowId: string }
  | { _id?: number; type: 'WORKFLOW_STATE_CHANGED'; to: string; workflowId: string };

// ─────────────────────────────────────────────────────────────────────────────
// 2. useWorkflowEvents — THE TRANSPORT HOOK
//
//    Owns: opening the EventSource, reconnect with capped exponential backoff,
//    and — critically — cleanup so no connection is ever leaked.
//
//    Notice: `onEvent` is stashed in a ref. This means the effect that opens the
//    connection does NOT need `onEvent` in its dependency array — so passing a
//    fresh inline arrow function from the parent on every render does not tear
//    down and reopen the SSE connection. Only `workflowId` changing should do that.
// ─────────────────────────────────────────────────────────────────────────────

function useWorkflowEvents(workflowId: string, onEvent: (e: AGUIEvent) => void) {
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent; // always points at the latest closure — no stale captures
  });

  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let es: EventSource;
    let backoff = 1000;
    let cancelled = false;

    function connect() {
      es = new EventSource(`/workflows/${workflowId}/stream`);

      es.onopen = () => {
        setConnected(true);
        backoff = 1000; // reset backoff on a healthy connection
      };

      es.onmessage = (raw: MessageEvent) => {
        const event = JSON.parse(raw.data) as AGUIEvent;
        onEventRef.current(event); // ← this is the call site that drives `dispatch`
      };

      es.onerror = () => {
        setConnected(false);
        es.close();
        if (!cancelled) {
          setTimeout(connect, backoff);
          backoff = Math.min(backoff * 2, 30_000); // cap at 30s — never hammer a recovering server
        }
      };
    }

    connect();

    return () => {
      cancelled = true;
      es.close(); // CLEANUP — without this, every mount/remount leaks an open connection
    };
  }, [workflowId]);

  return { connected };
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. useToolCalls — THE STATE-OWNING HOOK
//
//    Owns: the tool-call slice of state, and the `dispatch` function that mutates
//    it. Returns `dispatch` so a *caller* (the workbench component) can wire it
//    into the transport hook above. This hook knows NOTHING about SSE — it only
//    knows how to fold AG-UI events into a `ToolCall[]`.
//
//    The keyed merge is the core idea: TOOL_CALL_START appends a new row;
//    TOOL_CALL_END finds the SAME row by `id` and patches it in place. Matching
//    by id (not array index) is what keeps concurrent tool calls from colliding —
//    their END events can legitimately arrive out of order.
// ─────────────────────────────────────────────────────────────────────────────

export type ToolCall = {
  id: string;
  tool: string;
  status: 'running' | 'done';
  result?: unknown;
};

function useToolCalls() {
  const [calls, setCalls] = useState<ToolCall[]>([]);

  const dispatch = useCallback((event: AGUIEvent) => {
    switch (event.type) {
      case 'TOOL_CALL_START':
        setCalls((prev) => [
          ...prev,
          { id: event.id, tool: event.tool, status: 'running' },
        ]);
        return;

      case 'TOOL_CALL_END':
        setCalls((prev) =>
          prev.map((c) =>
            c.id === event.id // ← match by id, never by index
              ? { ...c, status: 'done', result: event.result }
              : c,
          ),
        );
        return;

      // This hook only cares about tool-call events — everything else is a no-op here.
      // (A real workbench would compose several such hooks, each owning its own slice,
      // and a top-level dispatch would fan the event to all of them.)
      default:
        return;
    }
  }, []); // stable identity — passing this into useWorkflowEvents won't reconnect the stream

  return { calls, dispatch };
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. RENDER — the 3-phase lifecycle, keyed and memoized
//
//    key={tc.id} tells React "this is the same row across renders, patch it in
//    place" rather than remount it — which is what lets a START→END transition
//    feel like an update, not a flicker.
//
//    memo() skips re-rendering a row whose props haven't changed — so when a
//    THIRD tool call appends to the array, the first two (already "done") don't
//    re-render. This is the case where memo earns its keep: expensive-ish rows,
//    re-rendering often with unchanged props.
// ─────────────────────────────────────────────────────────────────────────────

const ToolCallRow = memo(function ToolCallRow({ call }: { call: ToolCall }) {
  return (
    <div style={rowStyle}>
      <span>{call.status === 'running' ? '⏳' : '✅'}</span>
      <span style={{ marginLeft: 8, fontFamily: 'ui-monospace, monospace' }}>{call.tool}</span>
      {call.status === 'done' && (
        <details style={{ marginTop: 6 }}>
          <summary style={{ cursor: 'pointer', color: '#58a6ff' }}>result</summary>
          <pre style={preStyle}>{JSON.stringify(call.result, null, 2)}</pre>
        </details>
      )}
    </div>
  );
});

function ToolCallList({ calls }: { calls: ToolCall[] }) {
  if (calls.length === 0) {
    return <div style={{ color: '#8b949e', fontSize: 13 }}>No tool calls yet.</div>;
  }
  return (
    <>
      {calls.map((tc) => (
        <ToolCallRow key={tc.id} call={tc} />
      ))}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. useStreamingResponse — a SECOND state-owning hook, same shape as useToolCalls
//
//    Owns: the agent's streamed reasoning text. TEXT_MESSAGE_CONTENT events carry
//    a `delta` — a token fragment — and we append. This is the "response coming
//    in" piece: the same pattern as tool calls (own a slice, expose a dispatch),
//    just folding a different event type into a different shape of state.
//
//    Note the functional update `(prev) => prev + event.delta` — exactly the
//    "never read a stale capture" discipline from the stale-closure topic.
// ─────────────────────────────────────────────────────────────────────────────

function useStreamingResponse() {
  const [text, setText] = useState('');
  const [streaming, setStreaming] = useState(false);

  const dispatch = useCallback((event: AGUIEvent) => {
    switch (event.type) {
      case 'TEXT_MESSAGE_CONTENT':
        setStreaming(true);
        setText((prev) => prev + event.delta); // append — never overwrite, never read stale `text`
        return;
      case 'WORKFLOW_STATE_CHANGED':
        // a state change other than active streaming means the response is settled
        if (event.to !== 'INVESTIGATING') setStreaming(false);
        return;
      default:
        return;
    }
  }, []);

  return { text, streaming, dispatch };
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. useApprovalGate — a THIRD state-owning hook — approvals & workflow status
//
//    Owns: the approval gate and overall workflow status. APPROVAL_REQUEST seeds
//    the gate; APPROVAL_GRANTED patches it (signature arrives); WORKFLOW_STATE_CHANGED
//    updates status. Three different event types, one coherent slice of state —
//    because they all describe the SAME underlying decision lifecycle.
// ─────────────────────────────────────────────────────────────────────────────

function useApprovalGate() {
  const [status, setStatus] = useState('NEW');
  const [gate, setGate] = useState<Gate | null>(null);

  const dispatch = useCallback((event: AGUIEvent) => {
    switch (event.type) {
      case 'APPROVAL_REQUEST':
        setGate(event.gate);
        setStatus('AWAITING_APPROVAL');
        return;
      case 'APPROVAL_GRANTED':
        setGate(event.gate); // server is the source of truth — we just mirror its gate object
        return;
      case 'WORKFLOW_STATE_CHANGED':
        setStatus(event.to);
        return;
      default:
        return;
    }
  }, []);

  return { status, gate, dispatch };
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. useAGUIDispatch — THE GENERIC TOP-LEVEL FAN-OUT
//
//    This is the answer to "not just tool calls": a single hook that composes
//    every slice-owning hook above and returns ONE dispatch that fans each
//    incoming event to all of them. Each slice's own `switch` ignores event
//    types it doesn't care about (the `default: return` no-ops) — so adding a
//    new slice never requires touching the others. That's the scalability
//    property: N event types × M slices, with each slice only handling its own.
//
//    The exhaustive `never` check lives at the EVENT-TYPE level (the union
//    itself, see §1) — every member of AGUIEvent must exist in the type, and
//    each slice opts into the subset it cares about. This hook is what a real
//    <WorkflowWorkbench> would actually wire into useWorkflowEvents.
// ─────────────────────────────────────────────────────────────────────────────

function useAGUIDispatch() {
  const toolCalls = useToolCalls();
  const response = useStreamingResponse();
  const approval = useApprovalGate();

  // stable identity: fans one event to all three slices, in a fixed order
  const dispatch = useCallback(
    (event: AGUIEvent) => {
      toolCalls.dispatch(event);
      response.dispatch(event);
      approval.dispatch(event);
    },
    [toolCalls.dispatch, response.dispatch, approval.dispatch],
  );

  return { toolCalls: toolCalls.calls, response, approval, dispatch };
}

// ─────────────────────────────────────────────────────────────────────────────
// 8. <WorkflowWorkbench> — THE COMPOSITION ROOT
//
//    THIS is "who calls useToolCalls" (now: who calls useAGUIDispatch, which
//    itself calls useToolCalls): the component calls it once, gets back a fully
//    composed `dispatch`, and wires it into useWorkflowEvents as the onEvent
//    callback. From that point on, every SSE message — tool call, streamed
//    token, approval, state change — flows through ONE dispatch into THREE
//    independently-rendering slices.
//
//    Composition, not coupling: useWorkflowEvents owns transport, each `useX`
//    owns one state slice, useAGUIDispatch composes the slices, this component
//    just connects transport to composition.
// ─────────────────────────────────────────────────────────────────────────────

export function WorkflowWorkbench({ workflowId }: { workflowId: string }) {
  const { toolCalls, response, approval, dispatch } = useAGUIDispatch(); // ① composed state + fan-out dispatch
  const { connected } = useWorkflowEvents(workflowId, dispatch);          // ② wire it into the SSE transport

  return (
    <div style={panelStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: 13, color: '#8b949e', textTransform: 'uppercase' }}>
          Workflow — {workflowId} · <span style={{ color: '#58a6ff' }}>{approval.status}</span>
        </h3>
        <span style={{ fontSize: 12, color: connected ? '#3fb950' : '#f85149' }}>
          {connected ? '● live' : '○ connecting…'}
        </span>
      </div>

      {/* ③ streaming response — "response coming in" rendered live, token by token */}
      <Section title="Agent reasoning (streaming)">
        <div style={{ lineHeight: 1.5, color: '#c9d1d9', minHeight: 24 }}>
          {response.text}
          {response.streaming && <span style={cursorStyle} />}
        </div>
      </Section>

      {/* ④ tool calls — 3-phase lifecycle, keyed by id */}
      <Section title="Tool calls">
        <ToolCallList calls={toolCalls} />
      </Section>

      {/* ⑤ approval gate — who must sign, rendered from typed structured state */}
      {approval.gate && (
        <Section title="Approval gate">
          <div style={{ display: 'flex', gap: 10 }}>
            {approval.gate.requiredRoles.map((role) => {
              const sig = approval.gate!.signatures.find((s) => s.role === role);
              return (
                <div key={role} style={sigStyle(!!sig)}>
                  <div style={{ fontWeight: 700 }}>{role}</div>
                  <div style={{ fontSize: 11, color: '#8b949e' }}>
                    {sig ? `✔ ${sig.user}` : 'awaiting'}
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 14 }}>
      <h4 style={{ margin: '0 0 6px', fontSize: 11, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {title}
      </h4>
      {children}
    </div>
  );
}

const cursorStyle: React.CSSProperties = {
  display: 'inline-block',
  width: 7,
  height: 14,
  background: '#58a6ff',
  marginLeft: 2,
  verticalAlign: 'middle',
};

function sigStyle(signed: boolean): React.CSSProperties {
  return {
    flex: 1,
    padding: 10,
    borderRadius: 6,
    textAlign: 'center',
    border: signed ? '1px solid #3fb950' : '1px dashed #30363d',
    background: signed ? '#0f2417' : 'transparent',
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// styles (inline, kept minimal — this file is a reference, not a design system)
// ─────────────────────────────────────────────────────────────────────────────

const panelStyle: React.CSSProperties = {
  background: '#161b22',
  border: '1px solid #30363d',
  borderRadius: 8,
  padding: 14,
  color: '#e6edf3',
  fontFamily: '-apple-system, Segoe UI, Roboto, sans-serif',
  fontSize: 14,
};

const rowStyle: React.CSSProperties = {
  padding: '8px 10px',
  margin: '6px 0',
  background: '#0d1117',
  border: '1px solid #21262d',
  borderRadius: 6,
  fontSize: 13,
};

const preStyle: React.CSSProperties = {
  background: '#0d1117',
  padding: 8,
  borderRadius: 4,
  fontSize: 11,
  overflowX: 'auto',
  color: '#c9d1d9',
};

/**
 * ─────────────────────────────────────────────────────────────────────────────
 * THE END-TO-END TRACE — ALL EVENT TYPES, ONE DISPATCH, THREE SLICES
 * (read this aloud — it's the whole "not just tool calls" story in one trace)
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * Every SSE message takes the SAME path through transport and the SAME single
 * `dispatch` — what differs is which slice(s) react to it. Here are four
 * different event types landing on the one composed dispatch:
 *
 * ── TEXT_MESSAGE_CONTENT (the "response coming in") ──────────────────────────
 *   { type: "TEXT_MESSAGE_CONTENT", delta: "The exposure of " }
 *   → onEventRef.current(event) → useAGUIDispatch.dispatch(event)
 *       → toolCalls.dispatch(event)   → switch falls to `default: return` — no-op
 *       → response.dispatch(event)    → setText(prev => prev + "The exposure of ")
 *       → approval.dispatch(event)    → no-op
 *   → only <Section title="Agent reasoning"> re-renders; tool-call rows untouched
 *
 * ── TOOL_CALL_START / TOOL_CALL_END (the lifecycle pair) ─────────────────────
 *   { type: "TOOL_CALL_START", id: "tc_9", tool: "rag.search_csa" }
 *   → toolCalls.dispatch    → setCalls(prev => [...prev, { id:"tc_9", status:"running" }])
 *   → response.dispatch     → no-op   → approval.dispatch → no-op
 *   → <ToolCallRow key="tc_9"> mounts: "⏳ rag.search_csa"
 *   …3s later…
 *   { type: "TOOL_CALL_END", id: "tc_9", result: {...} }
 *   → toolCalls.dispatch    → setCalls(prev => prev.map(c => c.id==="tc_9" ? {...c,status:"done",result} : c))
 *   → React reconciles by key="tc_9" → PATCHES the same DOM node, no remount
 *   → row becomes: "✅ rag.search_csa  [result ▾]"
 *
 * ── APPROVAL_REQUEST (the gate appears) ──────────────────────────────────────
 *   { type: "APPROVAL_REQUEST", gate: { requiredRoles: ["RISK","MARGIN_OPS"], signatures: [] } }
 *   → toolCalls.dispatch → no-op   → response.dispatch → no-op
 *   → approval.dispatch  → setGate(event.gate); setStatus("AWAITING_APPROVAL")
 *   → <Section title="Approval gate"> mounts, header badge flips to AWAITING_APPROVAL
 *
 * ── WORKFLOW_STATE_CHANGED (status — read by MULTIPLE slices at once) ────────
 *   { type: "WORKFLOW_STATE_CHANGED", to: "CALL_ISSUED" }
 *   → toolCalls.dispatch → no-op
 *   → response.dispatch  → setStreaming(false)        (the response is now "settled")
 *   → approval.dispatch  → setStatus("CALL_ISSUED")   (header badge updates)
 *   → TWO slices react to the SAME event — this is exactly why fan-out beats a
 *     giant single reducer: each slice opts in to what it cares about, and a
 *     new slice added later can listen to existing event types with zero changes
 *     to the others.
 *
 * THE GENERALIZATION:
 *   `id` correlates a lifecycle PAIR (START↔END) into one row.
 *   `delta` correlates a SEQUENCE of fragments into one growing string.
 *   `gate`/`to` correlate a single SNAPSHOT-style payload into a slice's state.
 *   Three different correlation shapes, three different folding strategies
 *   (append-new, append-to-string, replace-whole) — but ONE transport, ONE
 *   exhaustive union, ONE dispatch contract. That uniformity is what lets you
 *   add a tenth event type without touching the SSE hook, the union's other
 *   members, or any slice that doesn't care about it.
 * ─────────────────────────────────────────────────────────────────────────────
 */
