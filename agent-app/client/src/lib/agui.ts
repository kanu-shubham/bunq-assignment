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
  | { type: "HITL_INTERRUPT"; interruptId: string; reason: string; payload: unknown }
  | { type: "STATE_SNAPSHOT"; state: unknown };

/**
 * fetch-based SSE consumer — `EventSource` doesn't support POST bodies, so we
 * read the response body line-by-line and parse `data:` frames ourselves.
 */
export async function streamAgUi(
  url: string,
  body: unknown,
  onEvent: (event: AgUiEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`stream failed: ${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      try {
        onEvent(JSON.parse(dataLine.slice(5).trim()) as AgUiEvent);
      } catch (err) {
        console.error("bad SSE frame", err, frame);
      }
    }
  }
}
