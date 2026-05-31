import { tool } from "@langchain/core/tools";
import { interrupt } from "@langchain/langgraph";
import { z } from "zod";

export const webSearch = tool(
  async ({ query }: { query: string }) => {
    // Mocked: pretend we hit a search index. Real impl would call Tavily / Brave / etc.
    return JSON.stringify({
      query,
      results: [
        { title: `Top result for "${query}"`, snippet: `Synthetic snippet about ${query}.` },
        { title: `Background on ${query}`, snippet: `More context about ${query}.` },
      ],
    });
  },
  {
    name: "web_search",
    description: "Search the web for information. Safe — runs without approval.",
    schema: z.object({ query: z.string().describe("Search query") }),
  },
);

export const runPython = tool(
  async ({ code }: { code: string }) => {
    // Mocked sandbox. Real impl would call a code-execution service.
    return JSON.stringify({ stdout: `[mock] would run:\n${code}`, exitCode: 0 });
  },
  {
    name: "run_python",
    description: "Execute a Python snippet in a sandbox. Safe — runs without approval.",
    schema: z.object({ code: z.string().describe("Python source") }),
  },
);

/**
 * Side-effectful tool — gated by a LangGraph `interrupt()`.
 *
 * The graph pauses here; the server emits a HITL_INTERRUPT event over AG-UI;
 * the human approves or rejects through the UI; the graph resumes with the
 * decision and either sends or aborts.
 */
export const sendEmail = tool(
  async ({ to, subject, body }: { to: string; subject: string; body: string }) => {
    const decision = interrupt({
      kind: "approve_email",
      to,
      subject,
      body,
      reason: "Sending email is irreversible — human approval required.",
    }) as { approved: boolean; note?: string };

    if (!decision?.approved) {
      return JSON.stringify({ sent: false, reason: decision?.note ?? "rejected by operator" });
    }
    // Mocked send.
    return JSON.stringify({ sent: true, to, subject, deliveredAt: new Date().toISOString() });
  },
  {
    name: "send_email",
    description: "Send an email. Requires human approval before sending.",
    schema: z.object({
      to: z.string().email(),
      subject: z.string(),
      body: z.string(),
    }),
  },
);

export const researcherTools = [webSearch];
export const coderTools = [runPython];
export const writerTools = [sendEmail];
export const allTools = [...researcherTools, ...coderTools, ...writerTools];
