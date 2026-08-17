/**
 * Bounded TTL cache that deliberately **keeps entries past their TTL**.
 *
 * The TTL says "is this fresh enough to serve without asking?"; it does not
 * say "throw this away". Retaining the expired value is the whole point of
 * pairing a cache with a circuit breaker: when the circuit is open, a stale
 * answer clearly labelled as stale beats an error page.
 *
 * Eviction is therefore by size (least-recently-used), not by age.
 */

export interface CacheLookup<T> {
  value: T;
  ageMs: number;
  isFresh: boolean;
}

export interface ResponseCacheOptions {
  /** How long an entry may be served without going to the network. */
  ttlMs?: number;
  maxEntries?: number;
  now?: () => number;
}

interface Entry<T> {
  value: T;
  storedAt: number;
}

const DEFAULTS = {
  ttlMs: 30_000,
  maxEntries: 500,
};

export class ResponseCache<T> {
  // Map preserves insertion order, so the first key is the LRU victim.
  private readonly entries = new Map<string, Entry<T>>();

  private readonly ttlMs: number;
  private readonly maxEntries: number;
  private readonly now: () => number;

  constructor(options: ResponseCacheOptions = {}) {
    this.ttlMs = options.ttlMs ?? DEFAULTS.ttlMs;
    this.maxEntries = options.maxEntries ?? DEFAULTS.maxEntries;
    this.now = options.now ?? (() => Date.now());
  }

  /** Returns stale entries too — read `isFresh` before serving. */
  get(key: string): CacheLookup<T> | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;

    // Re-insert to mark as recently used.
    this.entries.delete(key);
    this.entries.set(key, entry);

    const ageMs = this.now() - entry.storedAt;
    return { value: entry.value, ageMs, isFresh: ageMs < this.ttlMs };
  }

  set(key: string, value: T): void {
    this.entries.delete(key);
    this.entries.set(key, { value, storedAt: this.now() });

    while (this.entries.size > this.maxEntries) {
      const oldest = this.entries.keys().next();
      if (oldest.done) break;
      this.entries.delete(oldest.value);
    }
  }

  delete(key: string): void {
    this.entries.delete(key);
  }

  clear(): void {
    this.entries.clear();
  }

  get size(): number {
    return this.entries.size;
  }
}
