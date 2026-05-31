import { ChatAnthropic } from "@langchain/anthropic";
import {
  AIMessage,
  BaseMessage,
  HumanMessage,
  SystemMessage,
  ToolMessage,
} from "@langchain/core/messages";
import {
  Annotation,
  END,
  MemorySaver,
  START,
  StateGraph,
} from "@langchain/langgraph";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import type { StructuredToolInterface } from "@langchain/core/tools";
import { z } from "zod";
import { coderTools, researcherTools, writerTools } from "./tools.js";

const MODEL = process.env.MODEL ?? "claude-opus-4-8";

/**
 * Shared graph state — message history plus a `next` field the supervisor
 * writes to indicate which specialist (or END) handles the next turn.
 */
export const AgentState = Annotation.Root({
  messages: Annotation<BaseMessage[]>({
    reducer: (a, b) => a.concat(b),
    default: () => [],
  }),
  next: Annotation<string>({
    reducer: (_, b) => b,
    default: () => "supervisor",
  }),
});
export type AgentStateT = typeof AgentState.State;

const SPECIALISTS = ["researcher", "coder", "writer"] as const;
type Specialist = (typeof SPECIALISTS)[number];

const supervisorLlm = new ChatAnthropic({ model: MODEL, temperature: 0 });
const routeSchema = z.object({
  next: z.enum([...SPECIALISTS, "FINISH"] as [string, ...string[]]),
  reason: z.string(),
});

async function supervisor(state: AgentStateT) {
  const router = supervisorLlm.withStructuredOutput(routeSchema, { name: "route" });
  const decision = await router.invoke([
    new SystemMessage(
      `You are a supervisor coordinating three specialists: researcher (web lookup),
       coder (runs python), writer (drafts and sends email — requires human approval).
       Given the conversation so far, pick the next worker, or FINISH when the user's
       request is fully satisfied. Be decisive — do not loop.`,
    ),
    ...state.messages,
  ]);
  const next = decision.next === "FINISH" ? END : decision.next;
  return { next };
}

function makeAgentNode(name: Specialist, tools: StructuredToolInterface[], systemPrompt: string) {
  const llm = new ChatAnthropic({ model: MODEL, temperature: 0 }).bindTools(tools);
  return async (state: AgentStateT) => {
    const response = await llm.invoke([new SystemMessage(systemPrompt), ...state.messages]);
    // Tag the AI message with its author so the UI can attribute it.
    (response as AIMessage).name = name;
    return { messages: [response], next: name };
  };
}

const researcher = makeAgentNode(
  "researcher",
  researcherTools,
  "You research facts using web_search. Reply with concise findings; do not call other tools.",
);
const coder = makeAgentNode(
  "coder",
  coderTools,
  "You write and execute small Python snippets using run_python.",
);
const writer = makeAgentNode(
  "writer",
  writerTools,
  "You draft and send email using send_email. Confirm the recipient and subject before sending.",
);

function routeAfterAgent(state: AgentStateT): string {
  const last = state.messages.at(-1);
  if (last instanceof AIMessage && (last.tool_calls?.length ?? 0) > 0) {
    return `tools_${state.next}`;
  }
  return "supervisor";
}

const builder = new StateGraph(AgentState)
  .addNode("supervisor", supervisor)
  .addNode("researcher", researcher)
  .addNode("coder", coder)
  .addNode("writer", writer)
  .addNode("tools_researcher", new ToolNode(researcherTools))
  .addNode("tools_coder", new ToolNode(coderTools))
  .addNode("tools_writer", new ToolNode(writerTools))
  .addEdge(START, "supervisor")
  .addConditionalEdges("supervisor", (s) => s.next, {
    researcher: "researcher",
    coder: "coder",
    writer: "writer",
    [END]: END,
  })
  .addConditionalEdges("researcher", routeAfterAgent, {
    tools_researcher: "tools_researcher",
    supervisor: "supervisor",
  })
  .addConditionalEdges("coder", routeAfterAgent, {
    tools_coder: "tools_coder",
    supervisor: "supervisor",
  })
  .addConditionalEdges("writer", routeAfterAgent, {
    tools_writer: "tools_writer",
    supervisor: "supervisor",
  })
  .addEdge("tools_researcher", "researcher")
  .addEdge("tools_coder", "coder")
  .addEdge("tools_writer", "writer");

export const graph = builder.compile({ checkpointer: new MemorySaver() });

export function isToolMessage(m: BaseMessage): m is ToolMessage {
  return m.getType() === "tool";
}
export function isHumanMessage(m: BaseMessage): m is HumanMessage {
  return m.getType() === "human";
}
