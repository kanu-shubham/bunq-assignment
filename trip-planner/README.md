# Trip Planner — Mastra + AG-UI + CopilotKit demo

A single agent that exercises all four AG-UI features in one app:

1. **Streaming chat** — tokens stream via `TEXT_MESSAGE_*` events into `<CopilotChat>`.
2. **Tool calls** — the backend `getWeather` tool emits `TOOL_CALL_*` events.
3. **Human-in-the-loop** — the `confirmBooking` frontend action uses
   `renderAndWaitForResponse` to pause the run on Approve/Reject.
4. **Shared state** — `useCoAgent` + the `updateItinerary` frontend action keep
   the itinerary panel and the agent in sync via `STATE_SNAPSHOT / STATE_DELTA`.

The AG-UI bridge itself is a single call: `registerCopilotKit(...)` in
`src/mastra/index.ts`. Features 3 and 4 are frontend actions the agent calls by
name — the agent never knows how the UI is built.

## Run

```bash
# 1. Backend (Mastra, port 4111)
cp .env.example .env  # add OPENAI_API_KEY
npm install
npm run dev

# 2. Frontend (Next.js, port 3000) — in another terminal
cd frontend
npm install
npm run dev
```

Open http://localhost:3000 and try:

- "What's the weather in Lisbon?" → tool call
- "Add 3 days in Lisbon and 2 days in Porto" → shared state updates live
- "Book it" → approval dialog appears, agent waits for your click

## Layout

```
trip-planner/
├── src/mastra/
│   ├── index.ts                 # Mastra + registerCopilotKit (AG-UI bridge)
│   ├── agents/trip-planner.ts   # The single agent
│   └── tools/weather-tool.ts    # Backend tool (feature 2)
└── frontend/
    └── app/
        ├── page.tsx             # CopilotChat + useCoAgent + 2 frontend actions
        └── api/copilotkit/route.ts  # Proxies to Mastra at :4111/copilotkit
```
