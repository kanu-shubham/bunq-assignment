/**
 * The "existing request handler" the exercise asks you to retrofit:
 * `WebClient.execute(request)`, now wrapped in a circuit breaker with
 * response caching.
 *
 * Order of operations in `execute`:
 *
 *   1. fresh cache hit          → return it, no downstream call at all
 *   2. identical call in flight → join it (single-flight, no thundering herd)
 *   3. call downstream via the breaker, cache success
 *   4. call failed or refused   → serve a *stale* cache entry if we have one,
 *                                 flagged as stale, else rethrow
 *
 * Step 4 is why the two features belong together. A breaker on its own turns
 * a slow failure into a fast failure; a breaker plus a cache turns it into a
 * degraded success — which for a money app is the difference between
 * "something went wrong" and "your balance, as of two minutes ago".
 *
 * Caching is restricted to safe methods by default. Replaying a `POST
 * /transfers` out of a cache would duplicate someone's money, so a caller has
 * to opt into that explicitly and knowingly.
 */

import {
  CircuitBreaker,
  CircuitOpenError,
  type CircuitBreakerOptions,
  type CircuitState,
} from './circuitBreaker';
import { ResponseCache, type ResponseCacheOptions } from './responseCache';

export type HttpMethod = 'GET' | 'HEAD' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface HttpRequest {
  url: string;
  method?: HttpMethod;
  headers?: Readonly<Record<string, string>>;
  body?: unknown;
  /** Overrides the safe-method-only default. Opt in with care. */
  cacheable?: boolean;
  /**
   * Scopes the cache entry. In a multi-tenant app this must include the
   * user/account id, or one customer is served another's balance.
   */
  cacheKey?: string;
}

export interface HttpResponse<T = unknown> {
  status: number;
  body: T;
}

export const SOURCE = {
  NETWORK: 'network',
  CACHE_FRESH: 'cache-fresh',
  CACHE_STALE: 'cache-stale',
} as const;

export type ResponseSource = (typeof SOURCE)[keyof typeof SOURCE];

export interface ExecuteResult<T = unknown> {
  response: HttpResponse<T>;
  source: ResponseSource;
  /** Age of the served payload; 0 for a network response. */
  ageMs: number;
}

export class HttpError extends Error {
  readonly status: number;

  constructor(status: number, message = `HTTP ${status}`) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
  }
}

export type Transport = (request: HttpRequest) => Promise<HttpResponse>;

/**
 * Default failure policy. A 4xx means *we* sent a bad request — tripping the
 * breaker on it would take out a perfectly healthy downstream. 5xx, 429 and
 * anything non-HTTP (network error, timeout) count.
 */
export function isDownstreamFailure(error: unknown): boolean {
  if (error instanceof HttpError) {
    return error.status >= 500 || error.status === 429;
  }
  return true;
}

export interface WebClientOptions {
  transport: Transport;
  breaker?: CircuitBreaker;
  cache?: ResponseCache<HttpResponse>;
  breakerOptions?: CircuitBreakerOptions;
  cacheOptions?: ResponseCacheOptions;
  /** Fail static: serve stale on failure, not only when the circuit is open. */
  serveStaleOnFailure?: boolean;
}

const SAFE_METHODS: ReadonlySet<HttpMethod> = new Set<HttpMethod>(['GET', 'HEAD']);

export class WebClient {
  private readonly transport: Transport;
  private readonly breaker: CircuitBreaker;
  private readonly cache: ResponseCache<HttpResponse>;
  private readonly serveStaleOnFailure: boolean;
  private readonly inFlight = new Map<string, Promise<HttpResponse>>();

  constructor(options: WebClientOptions) {
    this.transport = options.transport;
    this.breaker =
      options.breaker ??
      new CircuitBreaker({ isFailure: isDownstreamFailure, ...options.breakerOptions });
    this.cache = options.cache ?? new ResponseCache<HttpResponse>(options.cacheOptions);
    this.serveStaleOnFailure = options.serveStaleOnFailure ?? true;
  }

  async execute<T = unknown>(request: HttpRequest): Promise<ExecuteResult<T>> {
    const method = request.method ?? 'GET';
    const cacheable = request.cacheable ?? SAFE_METHODS.has(method);
    const key = request.cacheKey ?? `${method} ${request.url}`;

    if (cacheable) {
      const hit = this.cache.get(key);
      if (hit?.isFresh) {
        return { response: hit.value as HttpResponse<T>, source: SOURCE.CACHE_FRESH, ageMs: hit.ageMs };
      }
    }

    try {
      const response = await this.dispatch(key, request, cacheable);
      return { response: response as HttpResponse<T>, source: SOURCE.NETWORK, ageMs: 0 };
    } catch (error) {
      const shouldFallBack =
        cacheable && (this.serveStaleOnFailure || error instanceof CircuitOpenError);

      if (shouldFallBack) {
        const stale = this.cache.get(key);
        if (stale) {
          return { response: stale.value as HttpResponse<T>, source: SOURCE.CACHE_STALE, ageMs: stale.ageMs };
        }
      }
      throw error;
    }
  }

  getCircuitState(): CircuitState {
    return this.breaker.getState();
  }

  private dispatch(key: string, request: HttpRequest, cacheable: boolean): Promise<HttpResponse> {
    // Single-flight: N callers asking for the same thing at the same time
    // produce one downstream call. Only for cacheable (safe) requests —
    // collapsing two POSTs would silently drop one.
    if (cacheable) {
      const existing = this.inFlight.get(key);
      if (existing) return existing;
    }

    const call = this.breaker
      .execute(() => this.transport(request))
      .then((response) => {
        if (cacheable) this.cache.set(key, response);
        return response;
      })
      .finally(() => {
        if (cacheable) this.inFlight.delete(key);
      });

    if (cacheable) this.inFlight.set(key, call);
    return call;
  }
}
