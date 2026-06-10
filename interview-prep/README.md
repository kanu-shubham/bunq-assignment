# Agentic UI — Interview Prep (JPMC Front End round)

A runnable, self-contained answer key for the likely technical questions.
Every file is annotated with the question it answers and the talking points.

```bash
cd interview-prep
npm install
npm run dev        # open the printed localhost URL
npm run typecheck  # strict TS, no emit
```

## Map: likely question → file

| Likely question | File |
|---|---|
| "Model the events an agent streams to the UI. How do you guarantee every variant is handled?" | `src/types/agentEvents.ts` (discriminated unions, `Extract`, type predicates, `assertNever`) |
| "Write a `defineTool` helper where `execute` gets typed args inferred from a Zod schema" | `src/types/generics.ts` |
| "An LLM returns JSON — how do you trust it?" | `src/schemas/llmOutput.ts` (Zod at the trust boundary, discriminated unions at runtime, streaming-partial-JSON caveat) |
| **"Build a chat UI that streams a response and renders a tool call as an approval card"** | `src/components/Chat.tsx`, `src/components/ToolApprovalCard.tsx`, `src/hooks/useChatStream.ts`, `src/server/mockAgent.ts` |
| "Explain Suspense / the `use()` hook / where RSC fits" | `src/demos/SuspenseDemo.tsx` |
| "What is concurrent rendering? When would you reach for useTransition?" | `src/demos/TransitionDemo.tsx` (toggle the checkbox and feel the jank) |
| "Implement optimistic UI with rollback" | `src/demos/OptimisticDemo.tsx` (type "fail" to watch automatic rollback) |
| "When does memoization actually matter?" | `src/demos/MemoDemo.tsx` (render counters + React Compiler caveat) |

## The 2-minute whiteboard script for the chat exercise

1. **Types first.** One discriminated union for wire events (`AgentStreamEvent`),
   one for UI messages (`ChatMessage`). Illegal states unrepresentable;
   `assertNever` makes unhandled variants a compile error.
2. **State.** A single `useReducer` — the stream is a sequence of events, so the
   UI is literally a reducer over them. Pure, testable, order-tolerant
   (text deltas upsert their message). Status is a finite enum:
   `idle | streaming | awaiting_approval | error`.
3. **Transport.** An async generator here; in production SSE
   (`fetch` + `ReadableStream`) or WebSocket. Same consumer either way.
   Cancellation = `generator.return()` / `AbortController`.
4. **HITL.** The run *pauses* at the tool call (LangGraph `interrupt()` pattern).
   Args are Zod-validated **before** the human sees them — invalid args can't
   even be approved. The card stays on screen with the decision + result as an
   audit record. Input is disabled while a decision is pending.
5. **Failure modes.** Agent-reported `run_error` vs transport exception both
   land in the same error state with a retry path. Stop button mid-stream.
   Never optimistically render an irreversible action as done.

## Rehearsing with the bunq assignment

The existing repo is genuinely good material for "walk me through a technical
decision" — same patterns, smaller scale:

- `src/features/feedback/state/feedbackMachine.ts` — you already chose a
  reducer/state-machine over scattered `useState`. Same argument as
  `useChatStream` here: finite states, illegal states unrepresentable, testable
  transitions. Say that sentence in the interview.
- `src/features/feedback/hooks/useFocusTrap.ts`, `useEscapeKey.ts` — a11y as
  composable hooks; maps directly to "production-grade, regulated environment".
- `src/features/feedback/services/feedbackService.ts` — service layer separated
  from components; maps to the transport/UI separation above.
