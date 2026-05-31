/**
 * AG-UI event protocol — minimal subset.
 *
 * Mirrors the spec at https://docs.ag-ui.com: every server-side event is one
 * JSON object that the client can render without consulting another endpoint.
 * Discriminated on `type` so both ends can pattern-match exhaustively.
 */

export type AgUiEvent =
  | { type: "RUN_STARTED"; runId: string; threadId: string }
  | { type: "RUN_FINISHED"; runId: string; reason: "complete" | "interrupted" | "error"; error?: string }
  | { type: "STEP_STARTED"; node: string }
  | { type: "STEP_FINISHED"; node: string }
  | { type: "AGENT_HANDOFF"; from: string; to: string }
  | { type: "TEXT_MESSAGE_START"; messageId: string; role: "assistant"; agent: string }
  | { type: "TEXT_MESSAGE_CONTENT"; messageId: string; delta: string }
  | { type: "TEXT_MESSAGE_END"; messageId: string }
  | { type: "TOOL_CALL_START"; toolCallId: string; name: string; agent: string }
  | { type: "TOOL_CALL_ARGS"; toolCallId: string; args: unknown }
  | { type: "TOOL_CALL_RESULT"; toolCallId: string; result: unknown; isError?: boolean }
  | {
      type: "HITL_INTERRUPT";
      interruptId: string;
      reason: string;
      payload: unknown;
    }
  | { type: "STATE_SNAPSHOT"; state: unknown };

export type SseEmitter = (event: AgUiEvent) => void;

/** Encode one event as an SSE `data:` frame. */
export function encodeSse(event: AgUiEvent): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}
