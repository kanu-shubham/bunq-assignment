/**
 * Circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED.
 *
 * Deliberate design choices, each of which is a follow-up question in the
 * interview this was written for:
 *
 * - **No background timers.** The OPEN → HALF_OPEN transition happens lazily,
 *   on the next call. A breaker holding a `setInterval` keeps the process
 *   alive and needs an explicit `dispose()`; a lazy check needs neither.
 * - **The clock is injected** (`now`), so tests are deterministic without
 *   fake timers.
 * - **Probes are permit-limited.** In HALF_OPEN only `halfOpenMaxConcurrent`
 *   calls reach the downstream. Letting every waiting request through the
 *   moment the window elapses is how a recovering service gets knocked over
 *   a second time.
 * - **Every state transition bumps a generation counter,** and each call
 *   carries the generation it was admitted under. JavaScript has no data
 *   races, but it very much has interleaving across `await`: a probe that
 *   resolves after the breaker has already reopened must not be allowed to
 *   close it. Stale results are dropped instead.
 * - **The breaker only decides.** It knows nothing about HTTP; which errors
 *   count as downstream failures is the caller's policy (`isFailure`), and
 *   *when enough have failed* is an injected `FailurePolicy`. The breaker owns
 *   transitions; it does not own the threshold arithmetic.
 */

import {
  ConsecutiveFailurePolicy,
  type FailurePolicy,
  type FailureSnapshot,
} from './failurePolicy';

export const CIRCUIT = {
  CLOSED: 'CLOSED',
  OPEN: 'OPEN',
  HALF_OPEN: 'HALF_OPEN',
} as const;

export type CircuitState = (typeof CIRCUIT)[keyof typeof CIRCUIT];

/** Thrown instead of calling downstream. Never counts as a failure itself. */
export class CircuitOpenError extends Error {
  readonly retryAfterMs: number;

  constructor(retryAfterMs: number, message = 'Circuit is open') {
    super(message);
    this.name = 'CircuitOpenError';
    this.retryAfterMs = retryAfterMs;
  }
}

/**
 * A hung downstream is the classic cause of a cascading failure: without a
 * per-call timeout the breaker never observes a failure and never opens.
 */
export class CallTimeoutError extends Error {
  readonly timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`Call timed out after ${timeoutMs}ms`);
    this.name = 'CallTimeoutError';
    this.timeoutMs = timeoutMs;
  }
}

export interface StateChange {
  from: CircuitState;
  to: CircuitState;
  at: number;
}

export interface CircuitBreakerOptions {
  /** Consecutive failures in CLOSED before opening. Sugar for the default policy. */
  failureThreshold?: number;
  /**
   * Overrides `failureThreshold` entirely. Inject a SlidingWindowFailurePolicy
   * to open on error *rate* rather than on a run of failures.
   */
  failurePolicy?: FailurePolicy;
  /** Consecutive successful probes in HALF_OPEN before closing. */
  successThreshold?: number;
  /** How long OPEN lasts before a probe is admitted. */
  openDurationMs?: number;
  /** Probes allowed through concurrently while HALF_OPEN. */
  halfOpenMaxConcurrent?: number;
  /** Per-call timeout; `null` disables it. */
  callTimeoutMs?: number | null;
  /** Which errors count against the downstream. Default: all of them. */
  isFailure?: (error: unknown) => boolean;
  now?: () => number;
  onStateChange?: (change: StateChange) => void;
}

export interface CircuitSnapshot {
  state: CircuitState;
  /** Whatever the injected failure policy tracks. */
  failure: FailureSnapshot;
  consecutiveSuccesses: number;
  openedAt: number | null;
  halfOpenPermits: number;
  generation: number;
}

/** Admission ticket handed out by `acquire`, settled by exactly one outcome. */
interface Permit {
  generation: number;
  probe: boolean;
}

const DEFAULTS = {
  failureThreshold: 5,
  successThreshold: 2,
  openDurationMs: 10_000,
  halfOpenMaxConcurrent: 1,
  callTimeoutMs: 5_000,
};

export class CircuitBreaker {
  private state: CircuitState = CIRCUIT.CLOSED;
  private consecutiveSuccesses = 0;
  private openedAt: number | null = null;
  private halfOpenPermits = 0;
  private generation = 0;

  private readonly failurePolicy: FailurePolicy;
  private readonly successThreshold: number;
  private readonly openDurationMs: number;
  private readonly halfOpenMaxConcurrent: number;
  private readonly callTimeoutMs: number | null;
  private readonly isFailure: (error: unknown) => boolean;
  private readonly now: () => number;
  private readonly onStateChange?: (change: StateChange) => void;

  constructor(options: CircuitBreakerOptions = {}) {
    this.failurePolicy =
      options.failurePolicy ??
      new ConsecutiveFailurePolicy(options.failureThreshold ?? DEFAULTS.failureThreshold);
    this.successThreshold = options.successThreshold ?? DEFAULTS.successThreshold;
    this.openDurationMs = options.openDurationMs ?? DEFAULTS.openDurationMs;
    this.halfOpenMaxConcurrent = options.halfOpenMaxConcurrent ?? DEFAULTS.halfOpenMaxConcurrent;
    this.callTimeoutMs = options.callTimeoutMs === undefined ? DEFAULTS.callTimeoutMs : options.callTimeoutMs;
    this.isFailure = options.isFailure ?? (() => true);
    this.now = options.now ?? (() => Date.now());
    this.onStateChange = options.onStateChange;
  }

  /**
   * Runs `fn` under the breaker.
   *
   * @throws {CircuitOpenError} without calling `fn` when the circuit is open,
   *   or when HALF_OPEN and another probe already holds the only permit.
   */
  async execute<T>(fn: () => Promise<T>): Promise<T> {
    // Admission is synchronous on purpose: no `await` may sit between reading
    // the state and taking the permit, or two callers could both take the
    // last one.
    const permit = this.acquire();

    try {
      const result = await this.callWithTimeout(fn);
      this.recordSuccess(permit);
      return result;
    } catch (error) {
      if (this.isFailure(error)) {
        this.recordFailure(permit);
      } else {
        // Not the downstream's fault (a 400, say). Don't count it — but do
        // hand the probe permit back, or HALF_OPEN deadlocks at zero permits.
        this.releaseProbe(permit);
      }
      throw error;
    }
  }

  getState(): CircuitState {
    return this.state;
  }

  snapshot(): CircuitSnapshot {
    return {
      state: this.state,
      failure: this.failurePolicy.snapshot(),
      consecutiveSuccesses: this.consecutiveSuccesses,
      openedAt: this.openedAt,
      halfOpenPermits: this.halfOpenPermits,
      generation: this.generation,
    };
  }

  /** Force back to CLOSED — for tests and for a manual operator override. */
  reset(): void {
    this.transitionTo(CIRCUIT.CLOSED);
    this.failurePolicy.reset();
    this.consecutiveSuccesses = 0;
    this.openedAt = null;
  }

  private acquire(): Permit {
    if (this.state === CIRCUIT.OPEN) {
      const elapsed = this.now() - (this.openedAt ?? 0);
      if (elapsed < this.openDurationMs) {
        throw new CircuitOpenError(this.openDurationMs - elapsed);
      }
      this.transitionTo(CIRCUIT.HALF_OPEN);
    }

    if (this.state === CIRCUIT.HALF_OPEN) {
      if (this.halfOpenPermits <= 0) {
        throw new CircuitOpenError(0, 'Circuit is half-open; a probe is already in flight');
      }
      this.halfOpenPermits -= 1;
      return { generation: this.generation, probe: true };
    }

    return { generation: this.generation, probe: false };
  }

  private recordSuccess(permit: Permit): void {
    if (permit.generation !== this.generation) return; // result from a past generation

    if (this.state === CIRCUIT.HALF_OPEN) {
      this.halfOpenPermits += 1;
      this.consecutiveSuccesses += 1;
      if (this.consecutiveSuccesses >= this.successThreshold) {
        this.transitionTo(CIRCUIT.CLOSED);
      }
      return;
    }

    this.failurePolicy.recordSuccess();
  }

  private recordFailure(permit: Permit): void {
    if (permit.generation !== this.generation) return;

    if (this.state === CIRCUIT.HALF_OPEN) {
      // One failed probe is enough. The downstream is still unwell, so reopen
      // immediately and restart the timer rather than spending the whole
      // failureThreshold budget on a service we already know is broken.
      this.transitionTo(CIRCUIT.OPEN);
      return;
    }

    this.failurePolicy.recordFailure();
    this.consecutiveSuccesses = 0;
    if (this.failurePolicy.shouldOpen()) {
      this.transitionTo(CIRCUIT.OPEN);
    }
  }

  private releaseProbe(permit: Permit): void {
    if (permit.probe && permit.generation === this.generation && this.state === CIRCUIT.HALF_OPEN) {
      this.halfOpenPermits += 1;
    }
  }

  private transitionTo(next: CircuitState): void {
    if (next === this.state) return;

    const from = this.state;
    this.state = next;
    this.generation += 1;

    switch (next) {
      case CIRCUIT.OPEN:
        this.openedAt = this.now();
        this.consecutiveSuccesses = 0;
        this.halfOpenPermits = 0;
        break;
      case CIRCUIT.HALF_OPEN:
        this.consecutiveSuccesses = 0;
        this.halfOpenPermits = this.halfOpenMaxConcurrent;
        break;
      case CIRCUIT.CLOSED:
        this.failurePolicy.reset();
        this.consecutiveSuccesses = 0;
        this.openedAt = null;
        this.halfOpenPermits = 0;
        break;
    }

    this.onStateChange?.({ from, to: next, at: this.now() });
  }

  private callWithTimeout<T>(fn: () => Promise<T>): Promise<T> {
    const ms = this.callTimeoutMs;
    if (ms === null) return fn();

    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new CallTimeoutError(ms)), ms);
    });

    return Promise.race([fn(), timeout]).finally(() => {
      if (timer !== undefined) clearTimeout(timer);
    });
  }
}
