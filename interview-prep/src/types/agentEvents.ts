/**
 * INTERVIEW TOPIC: Discriminated unions for agent message/event types.
 *
 * Likely questions:
 *  - "Model the events an agent backend streams to the UI."
 *  - "How do you make sure the UI handles every event type?" (exhaustiveness)
 *  - "How do you narrow a union to one variant?" (predicates, `switch` on the tag)
 *
 * This mirrors what AG-UI / Vercel AI SDK message `parts` look like in real life:
 * a single stream of tagged events, and the UI is a reducer over that stream.
 */

export interface ToolCall {
  id: string;
  name: string;
  /** Raw args as the LLM produced them — untrusted until Zod-validated at the boundary. */
  args: Record<string, unknown>;
}

/**
 * Everything the agent backend can send over the wire.
 * The `type` field is the discriminant: checking it narrows the whole object.
 */
export type AgentStreamEvent =
  | { type: 'run_started'; runId: string }
  | { type: 'text_delta'; messageId: string; delta: string }
  | { type: 'tool_call'; toolCall: ToolCall }
  | { type: 'tool_result'; toolCallId: string; result: string }
  | { type: 'run_finished'; runId: string }
  | { type: 'run_error'; message: string; retryable: boolean };

/**
 * The UI's message model — also a discriminated union, keyed on `role`.
 * Note how each variant carries only the fields that make sense for it;
 * an "everything optional on one interface" model would force defensive
 * checks everywhere and let illegal states exist.
 */
export type ChatMessage =
  | { id: string; role: 'user'; content: string }
  | { id: string; role: 'assistant'; content: string; streaming: boolean }
  | {
      id: string;
      role: 'tool';
      toolCall: ToolCall;
      status: 'pending' | 'approved' | 'rejected';
      result?: string;
    };

/** Narrowed aliases — Extract pulls one variant out of the union. */
export type AssistantMessage = Extract<ChatMessage, { role: 'assistant' }>;
export type ToolMessage = Extract<ChatMessage, { role: 'tool' }>;

/**
 * Type predicate: lets `messages.filter(isToolMessage)` return ToolMessage[]
 * instead of ChatMessage[].
 */
export function isToolMessage(m: ChatMessage): m is ToolMessage {
  return m.role === 'tool';
}

/**
 * Exhaustiveness check. In a `switch` over the union, the `default` branch
 * calls this with the leftover type. If someone adds a new event variant and
 * forgets to handle it, this line stops compiling — the bug is caught at
 * build time, not when an operator hits it in production.
 */
export function assertNever(x: never): never {
  throw new Error(`Unhandled variant: ${JSON.stringify(x)}`);
}
