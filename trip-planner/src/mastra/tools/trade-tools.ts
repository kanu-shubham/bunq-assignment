import { createTool } from "@mastra/core/tools";
import { z } from "zod";

// Mock trade book — deterministic so the demo runs with no external systems.
const trades: Record<string, {
  tradeId: string;
  instrument: string;
  notional: number;
  currency: string;
  counterparty: string;
  counterpartyLEI: string;
  expectedSettle: string;
  actualSettle: string | null;
  failReason: string;
  ourSSI: string;
  theirSSI: string;
}> = {
  "T-100012": {
    tradeId: "T-100012",
    instrument: "UST 10Y 4.25% Nov-34",
    notional: 5_000_000,
    currency: "USD",
    counterparty: "Citigroup Global Markets",
    counterpartyLEI: "MBNUM2BPBDO7JBLYG310",
    expectedSettle: "2026-05-27",
    actualSettle: null,
    failReason: "DK — standing settlement instruction mismatch (we expected DTCC, they used Euroclear)",
    ourSSI: "DTCC-001234",
    theirSSI: "EUROCLEAR-998877",
  },
  "T-100013": {
    tradeId: "T-100013",
    instrument: "Apple 4.5% 2030 (US037833DT06)",
    notional: 1_200_000,
    currency: "USD",
    counterparty: "Morgan Stanley & Co.",
    counterpartyLEI: "IGJSJL3JD5P30I6NJZ34",
    expectedSettle: "2026-05-27",
    actualSettle: "2026-05-27 (partial: 800,000)",
    failReason: "Partial delivery — 400,000 notional short, counterparty cites inventory pull",
    ourSSI: "EUROCLEAR-998877",
    theirSSI: "EUROCLEAR-998877",
  },
};

export const getTradeDetails = createTool({
  id: "getTradeDetails",
  description: "Retrieve trade details for a failed settlement by trade ID.",
  inputSchema: z.object({
    tradeId: z.string().describe("Trade ID, e.g. T-100012"),
  }),
  outputSchema: z.object({
    found: z.boolean(),
    trade: z.any().nullable(),
  }),
  execute: async ({ context }) => {
    const t = trades[context.tradeId];
    return { found: !!t, trade: t ?? null };
  },
});

export const draftCounterpartyMessage = createTool({
  id: "draftCounterpartyMessage",
  description:
    "Compose a short professional message to a counterparty about a settlement break.",
  inputSchema: z.object({
    counterparty: z.string(),
    tradeId: z.string(),
    issue: z.string().describe("Brief description of the break"),
    proposedAction: z.string().describe("What we'd like them to do"),
  }),
  outputSchema: z.object({ draft: z.string() }),
  execute: async ({ context }) => {
    const { counterparty, tradeId, issue, proposedAction } = context;
    const draft = [
      `Dear ${counterparty} operations team,`,
      ``,
      `Reference: ${tradeId}.`,
      `We have identified the following settlement issue: ${issue}.`,
      `Proposed resolution: ${proposedAction}.`,
      ``,
      `Please confirm receipt and expected resolution time.`,
      ``,
      `Regards,`,
      `Operations`,
    ].join("\n");
    return { draft };
  },
});
