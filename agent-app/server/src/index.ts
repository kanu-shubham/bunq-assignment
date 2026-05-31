import { serve } from "@hono/node-server";
import { AIMessage, BaseMessage, HumanMessage } from "@langchain/core/messages";
import { Command } from "@langchain/langgraph";
import { Hono } from "hono";
import { cors } from "hono/cors";
import { streamSSE } from "hono/streaming";
import { v4 as uuid } from "uuid";
import { AgUiEvent, encodeSse } from "./agui.js";
import { graph } from "./graph.js";

const app = new Hono();
app.use("*", cors({ origin: "*" }));

/**
 * Drives one execution of the graph (initial run OR resume from interrupt) and
 * emits AG-UI events. Reused by POST /runs and POST /runs/:threadId/resume.
 */
async function* runGraph(
  input: { messages: BaseMessage[] } | Command,
  threadId: string,
  runId: string,
): AsyncGenerator<AgUiEvent> {
  yield { type: "RUN_STARTED", runId, threadId };

  const config = { configurable: { thread_id: threadId }, version: "v2" as const };
  let lastAgent = "supervisor";
  const emittedTextIds = new Set<string>();

  try {
    for await (const event of graph.streamEvents(input as any, config)) {
      const ev = event.event;
      const node = event.metadata?.langgraph_node as string | undefined;

      if (ev === "on_chain_start" && node && node !== lastAgent && !node.startsWith("tools_")) {
        yield { type: "AGENT_HANDOFF", from: lastAgent, to: node };
        lastAgent = node;
      }

      if (ev === "on_chat_model_stream") {
        const chunk = event.data?.chunk as AIMessage | undefined;
        const text = typeof chunk?.content === "string" ? chunk.content : "";
        if (text) {
          const messageId = chunk?.id ?? `${node ?? lastAgent}-msg`;
          if (!emittedTextIds.has(messageId)) {
            yield { type: "TEXT_MESSAGE_START", messageId, role: "assistant", agent: node ?? lastAgent };
            emittedTextIds.add(messageId);
          }
          yield { type: "TEXT_MESSAGE_CONTENT", messageId, delta: text };
        }
      }

      if (ev === "on_chat_model_end") {
        const msg = event.data?.output as AIMessage | undefined;
        if (msg?.id && emittedTextIds.has(msg.id)) {
          yield { type: "TEXT_MESSAGE_END", messageId: msg.id };
          emittedTextIds.delete(msg.id);
        }
        for (const tc of msg?.tool_calls ?? []) {
          yield {
            type: "TOOL_CALL_START",
            toolCallId: tc.id ?? uuid(),
            name: tc.name,
            agent: node ?? lastAgent,
          };
          yield { type: "TOOL_CALL_ARGS", toolCallId: tc.id ?? "", args: tc.args };
        }
      }

      if (ev === "on_tool_end") {
        yield {
          type: "TOOL_CALL_RESULT",
          toolCallId: (event.data?.output as any)?.tool_call_id ?? "",
          result: (event.data?.output as any)?.content,
        };
      }
    }

    // Inspect post-run state — interrupted graphs leave `tasks[].interrupts` populated.
    const snapshot = await graph.getState(config);
    const pending = snapshot.tasks.flatMap((t) => t.interrupts ?? []);
    if (pending.length > 0) {
      for (const interrupt of pending) {
        yield {
          type: "HITL_INTERRUPT",
          interruptId: (interrupt as any).id ?? uuid(),
          reason: (interrupt as any).value?.reason ?? "approval required",
          payload: (interrupt as any).value,
        };
      }
      yield { type: "RUN_FINISHED", runId, reason: "interrupted" };
    } else {
      yield { type: "STATE_SNAPSHOT", state: { next: (snapshot.values as any).next } };
      yield { type: "RUN_FINISHED", runId, reason: "complete" };
    }
  } catch (err) {
    yield { type: "RUN_FINISHED", runId, reason: "error", error: (err as Error).message };
  }
}

app.post("/runs", async (c) => {
  const body = await c.req.json<{ message: string; threadId?: string }>();
  const threadId = body.threadId ?? uuid();
  const runId = uuid();

  return streamSSE(c, async (stream) => {
    const input = { messages: [new HumanMessage(body.message)] };
    for await (const event of runGraph(input, threadId, runId)) {
      await stream.write(encodeSse(event));
    }
  });
});

app.post("/runs/:threadId/resume", async (c) => {
  const threadId = c.req.param("threadId");
  const body = await c.req.json<{ approved: boolean; note?: string }>();
  const runId = uuid();

  return streamSSE(c, async (stream) => {
    const resume = new Command({ resume: { approved: body.approved, note: body.note } });
    for await (const event of runGraph(resume, threadId, runId)) {
      await stream.write(encodeSse(event));
    }
  });
});

app.get("/health", (c) => c.json({ ok: true, model: process.env.MODEL ?? "claude-opus-4-8" }));

const port = Number(process.env.PORT ?? 4000);
serve({ fetch: app.fetch, port }, (info) => {
  console.log(`agent server listening on http://localhost:${info.port}`);
});
