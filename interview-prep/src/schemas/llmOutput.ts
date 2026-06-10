/**
 * INTERVIEW TOPIC: Runtime validation of LLM structured outputs with Zod.
 *
 * The core talking point: TypeScript types are erased at runtime, and an LLM
 * is an untrusted data source — it can emit a negative amount, a made-up
 * currency, or malformed JSON. Every tool call crosses a trust boundary, so
 * it gets schema-validated before any UI renders it or any human approves it.
 * In a bank, "the model said so" is never enough.
 */
import { z } from 'zod';

/** Schema for the transfer_funds tool the mock agent calls. */
export const transferFundsArgs = z.object({
  amount: z.number().positive('amount must be positive'),
  currency: z.enum(['EUR', 'GBP', 'USD']),
  toIban: z
    .string()
    .regex(/^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$/, 'not a valid IBAN'),
  reference: z.string().max(140).optional(),
});

export type TransferFundsArgs = z.infer<typeof transferFundsArgs>;

/**
 * LLMs also produce *structured outputs* that aren't tool calls — e.g. a
 * triage decision for a reconciliation break. Model the variants as a
 * discriminated union in Zod, mirroring the TS pattern at runtime.
 */
export const triageDecision = z.discriminatedUnion('action', [
  z.object({
    action: z.literal('auto_resolve'),
    breakId: z.string(),
    confidence: z.number().min(0).max(1),
  }),
  z.object({
    action: z.literal('escalate'),
    breakId: z.string(),
    reason: z.string().min(1),
  }),
]);

export type TriageDecision = z.infer<typeof triageDecision>;

/**
 * Streaming caveat worth saying out loud in the interview: while tokens are
 * still arriving, the JSON is incomplete, so you either validate only on
 * completion, or use a partial/streaming parser (AI SDK's streamObject does
 * this) and treat in-flight values as provisional in the UI.
 */
export function parseFinalJson<TSchema extends z.ZodTypeAny>(
  schema: TSchema,
  rawText: string,
): z.SafeParseReturnType<unknown, z.infer<TSchema>> {
  let json: unknown;
  try {
    json = JSON.parse(rawText);
  } catch {
    // Surface "model emitted invalid JSON" the same way as a schema failure.
    return schema.safeParse(undefined);
  }
  return schema.safeParse(json);
}
