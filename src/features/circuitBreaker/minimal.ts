/**
 * The version to actually write in the interview.
 *
 * The full implementation in this folder is what you'd ship. It is far too
 * much to type in 40 minutes with two people watching. This is the subset
 * that still demonstrates every idea they are grading: the three states, the
 * permit-limited probe, the two thresholds, cache-on-success and serve-stale
 * -on-failure.
 *
 * ~90 lines. Type this, get it green, then let the follow-ups drive what you
 * add next — that is the shape of the round.
 */

export type State = 'CLOSED' | 'OPEN' | 'HALF_OPEN';

export class CircuitOpen extends Error {
  constructor(message = 'circuit open') {
    super(message);
    this.name = 'CircuitOpen';
  }
}

export class MiniBreaker {
  private state: State = 'CLOSED';
  private failures = 0;
  private successes = 0;
  private openedAt = 0;
  private probing = false;

  constructor(
    private readonly failureThreshold = 3,
    private readonly successThreshold = 2,
    private readonly openMs = 10_000,
    private readonly now: () => number = () => Date.now(),
  ) {}

  getState(): State {
    return this.state;
  }

  async execute<T>(fn: () => Promise<T>): Promise<T> {
    // Admission — all synchronous, before any await.
    if (this.state === 'OPEN') {
      if (this.now() - this.openedAt < this.openMs) throw new CircuitOpen();
      this.state = 'HALF_OPEN';
      this.successes = 0;
      this.probing = false;
    }
    if (this.state === 'HALF_OPEN') {
      if (this.probing) throw new CircuitOpen('probe already in flight');
      this.probing = true;
    }

    try {
      const result = await fn();
      this.onSuccess();
      return result;
    } catch (error) {
      this.onFailure();
      throw error;
    }
  }

  private onSuccess(): void {
    if (this.state === 'HALF_OPEN') {
      this.probing = false;
      this.successes += 1;
      if (this.successes >= this.successThreshold) this.close();
      return;
    }
    this.failures = 0;
  }

  private onFailure(): void {
    if (this.state === 'HALF_OPEN') {
      this.open(); // one bad probe is enough
      return;
    }
    this.failures += 1;
    if (this.failures >= this.failureThreshold) this.open();
  }

  private open(): void {
    this.state = 'OPEN';
    this.openedAt = this.now();
    this.probing = false;
    this.successes = 0;
  }

  private close(): void {
    this.state = 'CLOSED';
    this.failures = 0;
    this.successes = 0;
    this.probing = false;
  }
}

export interface MiniRequest {
  url: string;
  method?: string;
}

/** The "existing request handler", with the breaker and cache dropped in. */
export class MiniWebClient<T> {
  private readonly cache = new Map<string, { value: T; at: number }>();

  constructor(
    private readonly transport: (request: MiniRequest) => Promise<T>,
    private readonly breaker: MiniBreaker,
    private readonly ttlMs = 30_000,
    private readonly now: () => number = () => Date.now(),
  ) {}

  async execute(request: MiniRequest): Promise<{ value: T; stale: boolean }> {
    const method = request.method ?? 'GET';
    const key = `${method} ${request.url}`;
    // Only safe methods: replaying a POST /transfers moves the money twice.
    const cacheable = method === 'GET' || method === 'HEAD';
    const hit = cacheable ? this.cache.get(key) : undefined;

    if (hit && this.now() - hit.at < this.ttlMs) return { value: hit.value, stale: false };

    try {
      const value = await this.breaker.execute(() => this.transport(request));
      if (cacheable) this.cache.set(key, { value, at: this.now() });
      return { value, stale: false };
    } catch (error) {
      // Fail static: a stale answer beats an error page, if we have one.
      if (hit) return { value: hit.value, stale: true };
      throw error;
    }
  }
}
