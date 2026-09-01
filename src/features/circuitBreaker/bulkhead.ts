/**
 * Bulkhead: a cap on how many calls may be in flight at once.
 *
 * A circuit breaker reacts to a downstream that is *failing*. It does nothing
 * about one that is merely *slow* but still succeeding — and slow is the more
 * dangerous case, because every waiting call holds a connection, a promise
 * chain and its retained memory. One sluggish dependency quietly consumes the
 * whole connection pool and takes down the endpoints that had nothing to do
 * with it. The bulkhead is the wall between those compartments.
 *
 * Queued callers are served FIFO. The queue is bounded on purpose: an
 * unbounded one just relocates the outage from "too many connections" to
 * "out of memory", which is strictly worse because it is harder to see.
 */

export class BulkheadFullError extends Error {
  constructor(maxConcurrent: number, maxQueued: number) {
    super(`Bulkhead full: ${maxConcurrent} in flight, ${maxQueued} queued`);
    this.name = 'BulkheadFullError';
  }
}

export interface BulkheadOptions {
  maxConcurrent?: number;
  /** Callers admitted to wait. 0 means reject immediately when saturated. */
  maxQueued?: number;
}

export class Bulkhead {
  private readonly maxConcurrent: number;
  private readonly maxQueued: number;
  private inFlight = 0;
  private readonly queue: Array<() => void> = [];

  constructor(options: BulkheadOptions = {}) {
    this.maxConcurrent = options.maxConcurrent ?? 10;
    this.maxQueued = options.maxQueued ?? 20;
  }

  async execute<T>(fn: () => Promise<T>): Promise<T> {
    await this.acquire();
    try {
      return await fn();
    } finally {
      this.release();
    }
  }

  snapshot(): { inFlight: number; queued: number } {
    return { inFlight: this.inFlight, queued: this.queue.length };
  }

  private acquire(): Promise<void> {
    // Synchronous fast path: no `await` between the check and the increment,
    // so two callers in one tick cannot both take the last slot.
    if (this.inFlight < this.maxConcurrent) {
      this.inFlight += 1;
      return Promise.resolve();
    }

    if (this.queue.length >= this.maxQueued) {
      return Promise.reject(new BulkheadFullError(this.maxConcurrent, this.maxQueued));
    }

    return new Promise<void>((resolve) => {
      this.queue.push(() => {
        this.inFlight += 1;
        resolve();
      });
    });
  }

  private release(): void {
    this.inFlight -= 1;
    const next = this.queue.shift();
    if (next) next();
  }
}
