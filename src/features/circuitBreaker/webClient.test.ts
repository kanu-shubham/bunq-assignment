import { Bulkhead } from './bulkhead';
import { CircuitBreakerRegistry } from './breakerRegistry';
import { CIRCUIT, CircuitBreaker, CircuitOpenError } from './circuitBreaker';
import { ResponseCache } from './responseCache';
import {
  HttpError,
  SOURCE,
  WebClient,
  defaultBreakerKey,
  isDownstreamFailure,
  type HttpResponse,
  type Transport,
} from './webClient';

function makeClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    advance: (ms: number) => {
      t += ms;
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/** Wires a client whose breaker and cache share one injected clock. */
function makeClient(transport: Transport, ttlMs = 100) {
  const clock = makeClock();
  const breaker = new CircuitBreaker({
    failureThreshold: 2,
    successThreshold: 1,
    openDurationMs: 1_000,
    callTimeoutMs: null,
    isFailure: isDownstreamFailure,
    now: clock.now,
  });
  const cache = new ResponseCache<HttpResponse>({ ttlMs, now: clock.now });
  return { client: new WebClient({ transport, breaker, cache }), clock, breaker };
}

const balance: HttpResponse = { status: 200, body: { currency: 'EUR', amount: 42 } };

describe('WebClient.execute', () => {
  describe('caching', () => {
    it('caches a successful GET and serves it fresh without a second call', async () => {
      const transport = jest.fn<Promise<HttpResponse>, []>().mockResolvedValue(balance);
      const { client, clock } = makeClient(transport);

      const first = await client.execute({ url: '/balances' });
      expect(first).toEqual({ response: balance, source: SOURCE.NETWORK, ageMs: 0 });

      clock.advance(50);
      const second = await client.execute({ url: '/balances' });

      expect(second).toEqual({ response: balance, source: SOURCE.CACHE_FRESH, ageMs: 50 });
      expect(transport).toHaveBeenCalledTimes(1);
    });

    it('refetches once the entry is no longer fresh', async () => {
      const transport = jest.fn<Promise<HttpResponse>, []>().mockResolvedValue(balance);
      const { client, clock } = makeClient(transport);

      await client.execute({ url: '/balances' });
      clock.advance(101);
      const result = await client.execute({ url: '/balances' });

      expect(result.source).toBe(SOURCE.NETWORK);
      expect(transport).toHaveBeenCalledTimes(2);
    });

    it('never caches an unsafe method', async () => {
      const transport = jest.fn<Promise<HttpResponse>, []>().mockResolvedValue({ status: 201, body: { id: 't1' } });
      const { client } = makeClient(transport);

      // Replaying a transfer out of a cache would move the money twice.
      await client.execute({ url: '/transfers', method: 'POST', body: { amount: 10 } });
      await client.execute({ url: '/transfers', method: 'POST', body: { amount: 10 } });

      expect(transport).toHaveBeenCalledTimes(2);
    });

    it('scopes entries by cacheKey so one customer cannot see another', async () => {
      const transport = jest
        .fn<Promise<HttpResponse>, []>()
        .mockResolvedValueOnce({ status: 200, body: 'alice' })
        .mockResolvedValueOnce({ status: 200, body: 'bob' });
      const { client } = makeClient(transport);

      const alice = await client.execute({ url: '/balances', cacheKey: 'GET /balances#alice' });
      const bob = await client.execute({ url: '/balances', cacheKey: 'GET /balances#bob' });

      expect(alice.response.body).toBe('alice');
      expect(bob.response.body).toBe('bob');
      expect(transport).toHaveBeenCalledTimes(2);
    });

    it('collapses concurrent identical requests into one downstream call', async () => {
      const pending = deferred<HttpResponse>();
      const transport = jest.fn<Promise<HttpResponse>, []>().mockReturnValue(pending.promise);
      const { client } = makeClient(transport);

      const both = Promise.all([client.execute({ url: '/balances' }), client.execute({ url: '/balances' })]);
      pending.resolve(balance);

      const [a, b] = await both;
      expect(a.response).toEqual(balance);
      expect(b.response).toEqual(balance);
      expect(transport).toHaveBeenCalledTimes(1);
    });
  });

  describe('breaker integration', () => {
    it('opens the circuit after repeated 5xx and then stops calling downstream', async () => {
      const transport = jest.fn<Promise<HttpResponse>, []>().mockRejectedValue(new HttpError(503));
      const { client, breaker } = makeClient(transport);

      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(HttpError);
      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(HttpError);
      expect(breaker.getState()).toBe(CIRCUIT.OPEN);

      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(CircuitOpenError);
      expect(transport).toHaveBeenCalledTimes(2);
    });

    it('does not trip on a client error', async () => {
      const transport = jest.fn<Promise<HttpResponse>, []>().mockRejectedValue(new HttpError(404));
      const { client, breaker } = makeClient(transport);

      await expect(client.execute({ url: '/nope' })).rejects.toMatchObject({ status: 404 });
      await expect(client.execute({ url: '/nope' })).rejects.toMatchObject({ status: 404 });

      // Our bad request, not their outage — the downstream stays reachable.
      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);
    });

    it('recovers through a successful probe once the window elapses', async () => {
      const transport = jest
        .fn<Promise<HttpResponse>, []>()
        .mockRejectedValueOnce(new HttpError(503))
        .mockRejectedValueOnce(new HttpError(503))
        .mockResolvedValue(balance);
      const { client, clock, breaker } = makeClient(transport);

      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(HttpError);
      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(HttpError);
      expect(breaker.getState()).toBe(CIRCUIT.OPEN);

      clock.advance(1_000);
      const result = await client.execute({ url: '/balances' });

      expect(result.source).toBe(SOURCE.NETWORK);
      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);
    });
  });

  describe('failing static', () => {
    it('serves a stale entry, clearly aged, once the circuit is open', async () => {
      const transport = jest
        .fn<Promise<HttpResponse>, []>()
        .mockResolvedValueOnce(balance)
        .mockRejectedValue(new HttpError(503));
      const { client, clock, breaker } = makeClient(transport);

      await client.execute({ url: '/balances' });
      clock.advance(5_000);

      // Two failures trip the breaker; both still answer from cache.
      const first = await client.execute({ url: '/balances' });
      const second = await client.execute({ url: '/balances' });
      expect(breaker.getState()).toBe(CIRCUIT.OPEN);

      // And now the breaker refuses outright — still answered.
      const third = await client.execute({ url: '/balances' });

      for (const result of [first, second, third]) {
        expect(result.source).toBe(SOURCE.CACHE_STALE);
        expect(result.response).toEqual(balance);
        expect(result.ageMs).toBe(5_000);
      }
    });

    it('rethrows when the circuit is open and nothing is cached', async () => {
      const transport = jest.fn<Promise<HttpResponse>, []>().mockRejectedValue(new HttpError(503));
      const { client } = makeClient(transport);

      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(HttpError);
      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(HttpError);

      // No cached answer to degrade to, so the caller must handle the failure.
      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(CircuitOpenError);
    });

    it('does not serve stale data for an unsafe method', async () => {
      const transport = jest.fn<Promise<HttpResponse>, []>().mockRejectedValue(new HttpError(503));
      const { client } = makeClient(transport);

      await expect(
        client.execute({ url: '/transfers', method: 'POST', body: { amount: 10 } }),
      ).rejects.toBeInstanceOf(HttpError);
    });

    it('can be configured to fail static only when the circuit is open', async () => {
      const clock = makeClock();
      const transport = jest
        .fn<Promise<HttpResponse>, []>()
        .mockResolvedValueOnce(balance)
        .mockRejectedValue(new HttpError(503));
      const client = new WebClient({
        transport,
        serveStaleOnFailure: false,
        breaker: new CircuitBreaker({
          failureThreshold: 2,
          openDurationMs: 1_000,
          callTimeoutMs: null,
          isFailure: isDownstreamFailure,
          now: clock.now,
        }),
        cache: new ResponseCache<HttpResponse>({ ttlMs: 100, now: clock.now }),
      });

      await client.execute({ url: '/balances' });
      clock.advance(5_000);

      // While the circuit is closed the caller sees real errors...
      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(HttpError);
      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(HttpError);

      // ...and only once it opens do we degrade to the cached copy.
      const result = await client.execute({ url: '/balances' });
      expect(result.source).toBe(SOURCE.CACHE_STALE);
    });
  });
  describe('composition with retry, bulkhead and per-endpoint breakers', () => {
    it('stops retrying the moment the circuit opens', async () => {
      const transport = jest.fn<Promise<HttpResponse>, []>().mockRejectedValue(new HttpError(503));
      const clock = makeClock();
      const client = new WebClient({
        transport,
        retry: { maxAttempts: 5, sleep: () => Promise.resolve() },
        breaker: new CircuitBreaker({
          failureThreshold: 2,
          callTimeoutMs: null,
          isFailure: isDownstreamFailure,
          now: clock.now,
        }),
        cache: new ResponseCache<HttpResponse>({ now: clock.now }),
      });

      await expect(client.execute({ url: '/balances' })).rejects.toBeInstanceOf(CircuitOpenError);

      // Attempts 1 and 2 reach the transport and trip the breaker; attempt 3
      // is refused by it, and CircuitOpenError is not retryable — so we stop
      // rather than sleeping through attempts 4 and 5.
      expect(transport).toHaveBeenCalledTimes(2);
    });

    it('retries a transient failure and still caches the eventual success', async () => {
      const transport = jest
        .fn<Promise<HttpResponse>, []>()
        .mockRejectedValueOnce(new HttpError(503))
        .mockResolvedValue(balance);
      const clock = makeClock();
      const client = new WebClient({
        transport,
        retry: { maxAttempts: 3, sleep: () => Promise.resolve() },
        breaker: new CircuitBreaker({
          failureThreshold: 5,
          callTimeoutMs: null,
          isFailure: isDownstreamFailure,
          now: clock.now,
        }),
        cache: new ResponseCache<HttpResponse>({ ttlMs: 100, now: clock.now }),
      });

      const first = await client.execute({ url: '/balances' });
      expect(first.source).toBe(SOURCE.NETWORK);
      expect(transport).toHaveBeenCalledTimes(2);

      const second = await client.execute({ url: '/balances' });
      expect(second.source).toBe(SOURCE.CACHE_FRESH);
    });

    it('caps concurrent downstream calls with a bulkhead', async () => {
      const gates = [deferred<HttpResponse>(), deferred<HttpResponse>()];
      let call = 0;
      const transport = jest.fn<Promise<HttpResponse>, []>().mockImplementation(() => {
        const gate = gates[call];
        call += 1;
        return gate.promise;
      });
      const client = new WebClient({
        transport,
        bulkhead: new Bulkhead({ maxConcurrent: 1, maxQueued: 5 }),
        breakerOptions: { callTimeoutMs: null },
      });

      // Two different URLs, so single-flight does not collapse them.
      const both = Promise.all([client.execute({ url: '/a' }), client.execute({ url: '/b' })]);
      await Promise.resolve();

      // A slow dependency can only ever hold one connection here.
      expect(transport).toHaveBeenCalledTimes(1);

      gates[0].resolve(balance);
      await Promise.resolve();
      await Promise.resolve();
      expect(transport).toHaveBeenCalledTimes(2);

      gates[1].resolve(balance);
      await both;
    });

    it('keeps a sick endpoint from opening the circuit on a healthy one', async () => {
      const transport = jest.fn<Promise<HttpResponse>, [{ url: string }]>().mockImplementation((request) =>
        request.url.startsWith('/quotes') ? Promise.reject(new HttpError(503)) : Promise.resolve(balance),
      );
      const client = new WebClient({
        transport,
        breakers: CircuitBreakerRegistry.withOptions({
          failureThreshold: 1,
          callTimeoutMs: null,
          isFailure: isDownstreamFailure,
        }),
      });

      await expect(client.execute({ url: '/quotes/EUR-GBP' })).rejects.toBeInstanceOf(HttpError);
      expect(client.getCircuitState({ url: '/quotes/EUR-GBP' })).toBe(CIRCUIT.OPEN);

      // Different downstream, its own breaker, unaffected.
      const result = await client.execute({ url: '/balances' });
      expect(result.source).toBe(SOURCE.NETWORK);
      expect(client.getCircuitState({ url: '/balances' })).toBe(CIRCUIT.CLOSED);
    });

    it('groups concrete urls onto one breaker per route', () => {
      expect(defaultBreakerKey({ url: '/transfers/123' })).toBe('/transfers');
      expect(defaultBreakerKey({ url: '/transfers/456?expand=fees' })).toBe('/transfers');
      expect(defaultBreakerKey({ url: 'https://api.wise.com/v1/rates' })).toBe('https://api.wise.com');
    });
  });
});
