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

import { Bulkhead } from './bulkhead';
import { CircuitBreakerRegistry } from './breakerRegistry';
import {
  CircuitBreaker,
  CircuitOpenError,
  type CircuitBreakerOptions,
  type CircuitState,
} from './circuitBreaker';
import { ResponseCache, type ResponseCacheOptions } from './responseCache';
import { retry, type RetryOptions } from './retry';

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
  /** Which breaker this call belongs to. See `defaultBreakerKey`. */
  breakerKey?: string;
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

/**
 * Group calls that fail together. Keying on a concrete URL like
 * `/transfers/123` would give every transfer its own breaker with a sample
 * size of one, which never opens — so relative URLs collapse to their first
 * path segment, and absolute ones to their origin. Pass an explicit
 * `breakerKey` (a route template) when the default is too coarse.
 */
export function defaultBreakerKey(request: HttpRequest): string {
  const path = request.url.split('?')[0];
  try {
    return new URL(path).origin;
  } catch {
    const segment = path.split('/').filter(Boolean)[0];
    return segment ? `/${segment}` : '/';
  }
}

export interface WebClientOptions {
  transport: Transport;
  breaker?: CircuitBreaker;
  cache?: ResponseCache<HttpResponse>;
  breakerOptions?: CircuitBreakerOptions;
  cacheOptions?: ResponseCacheOptions;
  /** Fail static: serve stale on failure, not only when the circuit is open. */
  serveStaleOnFailure?: boolean;
  /** One breaker per downstream instead of one per client. */
  breakers?: CircuitBreakerRegistry;
  breakerKeyFor?: (request: HttpRequest) => string;
  /** Caps concurrent downstream calls, so a *slow* dependency can't drain the pool. */
  bulkhead?: Bulkhead;
  /** Omit to disable retries. */
  retry?: RetryOptions;
}

const SAFE_METHODS: ReadonlySet<HttpMethod> = new Set<HttpMethod>(['GET', 'HEAD']);

export class WebClient {
  private readonly transport: Transport;
  private readonly cache: ResponseCache<HttpResponse>;
  private readonly serveStaleOnFailure: boolean;
  private readonly resolveBreaker: (request: HttpRequest) => CircuitBreaker;
  private readonly bulkhead?: Bulkhead;
  private readonly retryOptions?: RetryOptions;
  private readonly inFlight = new Map<string, Promise<HttpResponse>>();

  constructor(options: WebClientOptions) {
    this.transport = options.transport;
    this.cache = options.cache ?? new ResponseCache<HttpResponse>(options.cacheOptions);
    this.serveStaleOnFailure = options.serveStaleOnFailure ?? true;
    this.bulkhead = options.bulkhead;
    this.retryOptions = options.retry;

    if (options.breakers) {
      const registry = options.breakers;
      const keyFor = options.breakerKeyFor ?? ((request) => request.breakerKey ?? defaultBreakerKey(request));
      this.resolveBreaker = (request) => registry.get(keyFor(request));
    } else {
      const single =
        options.breaker ??
        new CircuitBreaker({ isFailure: isDownstreamFailure, ...options.breakerOptions });
      this.resolveBreaker = () => single;
    }
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

  /** Pass the request when the client is backed by a registry of breakers. */
  getCircuitState(request: HttpRequest = { url: '' }): CircuitState {
    return this.resolveBreaker(request).getState();
  }

  private dispatch(key: string, request: HttpRequest, cacheable: boolean): Promise<HttpResponse> {
    // Single-flight: N callers asking for the same thing at the same time
    // produce one downstream call. Only for cacheable (safe) requests —
    // collapsing two POSTs would silently drop one.
    if (cacheable) {
      const existing = this.inFlight.get(key);
      if (existing) return existing;
    }

    // Order matters, and it is the standard one:
    //   retry( breaker( bulkhead( timeout( transport ) ) ) )
    // Retry is outermost so the breaker sees every attempt and so an open
    // circuit short-circuits the retry loop instead of sleeping through it.
    // The bulkhead is innermost because it is capping real connections.
    const breaker = this.resolveBreaker(request);
    const attempt = () => breaker.execute(() => this.send(request));

    const call = (this.retryOptions ? retry(attempt, this.retryOptions) : attempt())
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

  private send(request: HttpRequest): Promise<HttpResponse> {
    if (!this.bulkhead) return this.transport(request);
    return this.bulkhead.execute(() => this.transport(request));
  }
}
