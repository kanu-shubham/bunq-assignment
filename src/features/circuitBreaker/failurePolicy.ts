/**
 * When should the circuit open?
 *
 * The naive answer — "N failures in a row" — is what you write first, and it
 * is wrong in one specific way an interviewer will press on: it cannot tell
 * 5 failures out of 5 apart from 5 failures out of 5000. A downstream at a 40%
 * error rate never produces a run of 5, so the breaker never opens, and every
 * other customer request fails.
 *
 * So the threshold is a strategy, injected into the breaker. The breaker owns
 * *state transitions*; a policy owns *the opening decision*. Swapping in a
 * time-based window later means writing one more class here, and not touching
 * the breaker at all.
 */

export interface FailureSnapshot {
  /** Whatever the policy wants to expose to metrics. */
  [metric: string]: number;
}

export interface FailurePolicy {
  recordSuccess(): void;
  recordFailure(): void;
  /** Consulted after every recorded outcome while CLOSED. */
  shouldOpen(): boolean;
  reset(): void;
  snapshot(): FailureSnapshot;
}

/** N consecutive failures. Cheap, predictable, and blind to error *rate*. */
export class ConsecutiveFailurePolicy implements FailurePolicy {
  private consecutiveFailures = 0;

  constructor(private readonly threshold: number) {}

  recordSuccess(): void {
    this.consecutiveFailures = 0;
  }

  recordFailure(): void {
    this.consecutiveFailures += 1;
  }

  shouldOpen(): boolean {
    return this.consecutiveFailures >= this.threshold;
  }

  reset(): void {
    this.consecutiveFailures = 0;
  }

  snapshot(): FailureSnapshot {
    return { consecutiveFailures: this.consecutiveFailures };
  }
}

export interface SlidingWindowOptions {
  /** How many recent calls to remember. */
  windowSize?: number;
  /** Open at or above this failure rate, 0..1. */
  failureRateThreshold?: number;
  /**
   * Don't judge a downstream on too little evidence. Without this, the first
   * call failing is a 100% failure rate and the circuit opens instantly.
   */
  minimumCalls?: number;
}

/**
 * Failure *rate* over the last N calls, held in a fixed-size ring buffer:
 * O(1) per record, O(1) memory, no timestamps to sweep.
 */
export class SlidingWindowFailurePolicy implements FailurePolicy {
  private readonly outcomes: boolean[];
  private readonly windowSize: number;
  private readonly failureRateThreshold: number;
  private readonly minimumCalls: number;

  private cursor = 0;
  private recorded = 0;
  private failures = 0;

  constructor(options: SlidingWindowOptions = {}) {
    this.windowSize = options.windowSize ?? 20;
    this.failureRateThreshold = options.failureRateThreshold ?? 0.5;
    this.minimumCalls = options.minimumCalls ?? 10;
    this.outcomes = new Array<boolean>(this.windowSize).fill(false);
  }

  recordSuccess(): void {
    this.record(false);
  }

  recordFailure(): void {
    this.record(true);
  }

  shouldOpen(): boolean {
    if (this.recorded < this.minimumCalls) return false;
    return this.failures / this.recorded >= this.failureRateThreshold;
  }

  reset(): void {
    this.outcomes.fill(false);
    this.cursor = 0;
    this.recorded = 0;
    this.failures = 0;
  }

  snapshot(): FailureSnapshot {
    return {
      calls: this.recorded,
      failures: this.failures,
      failureRate: this.recorded === 0 ? 0 : this.failures / this.recorded,
    };
  }

  private record(isFailure: boolean): void {
    if (this.recorded === this.windowSize) {
      // Window is full: the entry we're about to overwrite leaves the window.
      if (this.outcomes[this.cursor]) this.failures -= 1;
    } else {
      this.recorded += 1;
    }

    this.outcomes[this.cursor] = isFailure;
    if (isFailure) this.failures += 1;
    this.cursor = (this.cursor + 1) % this.windowSize;
  }
}
