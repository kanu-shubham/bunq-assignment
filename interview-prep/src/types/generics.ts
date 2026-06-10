/**
 * INTERVIEW TOPIC: Generics + inference, applied to agent tooling.
 *
 * Likely question: "Define a tool so that its `execute` function gets
 * fully-typed args, inferred from its schema — no casts."
 *
 * This is exactly how the Vercel AI SDK's `tool()` helper works, so being
 * able to write it from scratch shows you understand the framework rather
 * than just consuming it.
 */
import { z } from 'zod';

export interface ToolDef<TSchema extends z.ZodTypeAny, TResult> {
  name: string;
  description: string;
  schema: TSchema;
  /** `z.infer` turns the runtime schema into a static type — one source of truth. */
  execute: (args: z.infer<TSchema>) => Promise<TResult>;
}

/**
 * Identity function whose only job is inference: callers never write the
 * type parameters, TypeScript derives them from the schema they pass.
 */
export function defineTool<TSchema extends z.ZodTypeAny, TResult>(
  def: ToolDef<TSchema, TResult>,
): ToolDef<TSchema, TResult> {
  return def;
}

// --- Usage: args are typed as { amount: number; iban: string } inside execute ---
export const transferTool = defineTool({
  name: 'transfer_funds',
  description: 'Transfer money to an IBAN',
  schema: z.object({ amount: z.number().positive(), iban: z.string() }),
  execute: async (args) => {
    return `Transferred €${args.amount.toFixed(2)} to ${args.iban}`;
  },
});

/**
 * Follow-up question: "Type a function that validates unknown LLM output
 * against any schema and returns a typed result." — generic over the schema,
 * returns a discriminated union instead of throwing.
 */
export type Validated<T> =
  | { ok: true; data: T }
  | { ok: false; issues: string[] };

export function validateLLMOutput<TSchema extends z.ZodTypeAny>(
  schema: TSchema,
  raw: unknown,
): Validated<z.infer<TSchema>> {
  const parsed = schema.safeParse(raw);
  return parsed.success
    ? { ok: true, data: parsed.data }
    : { ok: false, issues: parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`) };
}
