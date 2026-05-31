# Agent App — LangGraph.js · AG-UI · Claude

A TypeScript end-to-end demo combining three patterns from the JD: **HITL supervisor**, **chat + tools**, and **multi-agent coordination**.

## Architecture

```
client (Vite + React)
   │  POST /runs                 (chat)
   │  POST /runs/:id/resume      (approve / reject HITL interrupt)
   ▼  SSE  ──────────────────────────────────────────────────────────
server (Hono + Node)
   │
   ▼
LangGraph supervisor
   ├─ researcher  → web_search           (no approval)
   ├─ coder       → run_python           (no approval)
   └─ writer      → send_email           (interrupt() → HITL approval)
```

The server translates LangGraph's `streamEvents` into [AG-UI](https://docs.ag-ui.com) events
(`RUN_STARTED`, `AGENT_HANDOFF`, `TEXT_MESSAGE_*`, `TOOL_CALL_*`, `HITL_INTERRUPT`, `RUN_FINISHED`)
streamed as SSE. The client maintains a live trace and renders an approval modal whenever the
graph pauses on a side-effectful tool.

## Run

```sh
export ANTHROPIC_API_KEY=sk-ant-...
cd agent-app
npm install
npm run dev      # server on :4000, client on :5173 (proxied)
```

Open http://localhost:5173 and try:

- *"Look up the latest on FX settlement T+1"* → researcher streams findings
- *"Calculate the std dev of [3, 1, 4, 1, 5, 9, 2, 6]"* → coder runs python
- *"Email alice@example.com a summary of our last research turn"* → writer pauses for approval

## Files

```
server/src/
├── index.ts   Hono routes + LangGraph event → AG-UI translation
├── graph.ts   Supervisor + researcher / coder / writer nodes
├── tools.ts   web_search, run_python, send_email (interrupt-gated)
└── agui.ts    AG-UI event types + SSE encoder

client/src/
├── App.tsx                       state machine for AG-UI events
├── lib/agui.ts                   fetch-based SSE consumer + types
└── components/
    ├── Trace.tsx                 handoffs / streamed text / tool calls
    └── ApprovalModal.tsx         HITL approve / reject
```

## Model

Defaults to `claude-opus-4-8`. Override with `MODEL=claude-sonnet-4-6` (faster, cheaper) before
starting the server.
