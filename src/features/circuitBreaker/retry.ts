/**
 * Retry with exponential backoff and full jitter.
 *
 * The composition matters more than the code, and it is the thing to say out
 * loud: **retry wraps the breaker, not the other way round.**
 *
 *   retry( breaker( bulkhead( transport ) ) )
 *
 * That ordering gives two properties you want and one you must avoid:
 *
 * - The breaker observes *every* attempt, so a retrying client fills the
 *   failure window faster rather than hiding the outage from it.
 * - Once the breaker opens, the retry loop gets `CircuitOpenError`, which is
 *   deliberately **not retryable** — so the whole call fails in microseconds
 *   instead of sleeping through three backoff delays to reach a downstream
 *   we already know is refusing traffic.
 * - Retry *inside* the breaker would make one logical call look like a single
 *   outcome while landing N requests on a struggling service: retry storms
 *   are how a partial outage becomes a total one.
 *
 * Jitter is not a nicety. Plain exponential backoff synchronises every client
 * that failed at the same moment onto the same retry instants, so a recovering
 * service is hit by a fleet-wide thundering herd at t+1s, t+2s, t+4s. Full
 * jitter — a uniform random pick in [0, delay] — spreads them out.
 */

import { CircuitOpenError } from './circuitBreaker';
import { BulkheadFullError } from './bulkhead';

export interface RetryOptions {
  /** Total attempts, including the first. */
  maxAttempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  isRetryable?: (error: unknown) => boolean;
  /** Injected for deterministic tests. */
  random?: () => number;
  sleep?: (ms: number) => Promise<void>;
  onRetry?: (attempt: number, delayMs: number, error: unknown) => void;
}

/**
 * Default: don't retry what retrying cannot fix. A refused call never reached
 * the downstream, so trying again changes nothing until the state does.
 */
export function isRetryableError(error: unknown): boolean {
  if (error instanceof CircuitOpenError) return false;
  if (error instanceof BulkheadFullError) return false;
  return true;
}

const DEFAULTS = {
  maxAttempts: 3,
  baseDelayMs: 100,
  maxDelayMs: 2_000,
};

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function retry<T>(fn: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const maxAttempts = options.maxAttempts ?? DEFAULTS.maxAttempts;
  const baseDelayMs = options.baseDelayMs ?? DEFAULTS.baseDelayMs;
  const maxDelayMs = options.maxDelayMs ?? DEFAULTS.maxDelayMs;
  const isRetryable = options.isRetryable ?? isRetryableError;
  const random = options.random ?? Math.random;
  const sleep = options.sleep ?? defaultSleep;

  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;

      const isLastAttempt = attempt === maxAttempts;
      if (isLastAttempt || !isRetryable(error)) throw error;

      // Full jitter: uniform in [0, min(cap, base * 2^(attempt-1))].
      const ceiling = Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1));
      const delayMs = Math.floor(random() * ceiling);

      options.onRetry?.(attempt, delayMs, error);
      await sleep(delayMs);
    }
  }

  throw lastError;
}
