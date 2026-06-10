/**
 * Mock agent backend, exposed as async generators.
 *
 * In production this would be an SSE endpoint (`fetch` + ReadableStream) or a
 * WebSocket; an async generator has the same shape — a pull-based stream of
 * events — so the hook consuming it wouldn't change. That separation is the
 * whiteboard point: the UI is a reducer over AgentStreamEvent, regardless of
 * transport.
 *
 * The run *pauses* at the tool call (LangGraph `interrupt()` style): the first
 * generator ends after emitting `tool_call`, and the client resumes the run
 * with the human's decision via `resumeAgent`.
 */
import type { AgentStreamEvent, ToolCall } from '../types/agentEvents';

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

let idCounter = 0;
const nextId = (prefix: string) => `${prefix}_${++idCounter}`;

function* tokenize(text: string): Generator<string> {
  for (const word of text.split(/(?<=\s)/)) yield word;
}

async function* streamText(
  messageId: string,
  text: string,
): AsyncGenerator<AgentStreamEvent> {
  for (const token of tokenize(text)) {
    await sleep(35);
    yield { type: 'text_delta', messageId, delta: token };
  }
}

export interface RunOptions {
  /** 0..1 — chance the run dies mid-stream, to exercise the error path. */
  failureRate?: number;
  /** 0..1 — chance the LLM emits invalid tool args, to exercise Zod at the boundary. */
  badArgsRate?: number;
}

export async function* runAgent(
  userText: string,
  { failureRate = 0, badArgsRate = 0.3 }: RunOptions = {},
): AsyncGenerator<AgentStreamEvent> {
  const runId = nextId('run');
  yield { type: 'run_started', runId };

  if (Math.random() < failureRate) {
    await sleep(400);
    yield { type: 'run_error', message: 'Upstream model timed out', retryable: true };
    return;
  }

  yield* streamText(
    nextId('msg'),
    `You asked: “${userText.trim()}”. I can do that, but it moves money, ` +
      `so I need your approval before executing the transfer.`,
  );

  const badArgs = Math.random() < badArgsRate;
  const toolCall: ToolCall = {
    id: nextId('tool'),
    name: 'transfer_funds',
    // The "LLM" sometimes hallucinates a negative amount — the UI must catch it.
    args: badArgs
      ? { amount: -250, currency: 'EUR', toIban: 'not-an-iban' }
      : { amount: 250, currency: 'EUR', toIban: 'NL91BUNQ0417164300', reference: 'Invoice 0042' },
  };
  yield { type: 'tool_call', toolCall };
  // Run pauses here, awaiting human approval (HITL interrupt).
}

export async function* resumeAgent(
  toolCall: ToolCall,
  decision: 'approved' | 'rejected',
): AsyncGenerator<AgentStreamEvent> {
  const runId = nextId('run');
  yield { type: 'run_started', runId };
  await sleep(500);

  if (decision === 'approved') {
    const { amount, currency, toIban } = toolCall.args as Record<string, unknown>;
    yield {
      type: 'tool_result',
      toolCallId: toolCall.id,
      result: `Executed: ${String(amount)} ${String(currency)} → ${String(toIban)}`,
    };
    yield* streamText(nextId('msg'), 'Done — the transfer was executed and logged to the audit trail.');
  } else {
    yield {
      type: 'tool_result',
      toolCallId: toolCall.id,
      result: 'Cancelled by operator',
    };
    yield* streamText(nextId('msg'), 'Understood, I cancelled the transfer. Nothing was executed.');
  }

  yield { type: 'run_finished', runId };
}
