import { CallTimeoutError, CIRCUIT, CircuitBreaker, CircuitOpenError, type StateChange } from './circuitBreaker';

/** Injected clock — deterministic state timing without fake timers. */
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
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const ok = () => Promise.resolve('ok');
const boom = () => Promise.reject(new Error('downstream exploded'));

describe('CircuitBreaker', () => {
  describe('CLOSED', () => {
    it('stays closed while calls succeed', async () => {
      const breaker = new CircuitBreaker({ failureThreshold: 2, callTimeoutMs: null });

      await expect(breaker.execute(ok)).resolves.toBe('ok');
      await expect(breaker.execute(ok)).resolves.toBe('ok');

      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);
    });

    it('opens after failureThreshold consecutive failures', async () => {
      const breaker = new CircuitBreaker({ failureThreshold: 3, callTimeoutMs: null });

      await expect(breaker.execute(boom)).rejects.toThrow('downstream exploded');
      await expect(breaker.execute(boom)).rejects.toThrow('downstream exploded');
      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);

      await expect(breaker.execute(boom)).rejects.toThrow('downstream exploded');
      expect(breaker.getState()).toBe(CIRCUIT.OPEN);
    });

    it('counts consecutive failures only — a success resets the run', async () => {
      const breaker = new CircuitBreaker({ failureThreshold: 3, callTimeoutMs: null });

      await expect(breaker.execute(boom)).rejects.toThrow();
      await expect(breaker.execute(boom)).rejects.toThrow();
      await expect(breaker.execute(ok)).resolves.toBe('ok');
      await expect(breaker.execute(boom)).rejects.toThrow();
      await expect(breaker.execute(boom)).rejects.toThrow();

      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);
      expect(breaker.snapshot().consecutiveFailures).toBe(2);
    });

    it('does not trip on errors the policy excludes, e.g. a client error', async () => {
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        callTimeoutMs: null,
        isFailure: (error) => !(error instanceof RangeError),
      });

      await expect(breaker.execute(() => Promise.reject(new RangeError('bad input')))).rejects.toThrow('bad input');

      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);
      expect(breaker.snapshot().consecutiveFailures).toBe(0);
    });
  });

  describe('OPEN', () => {
    it('rejects without calling downstream, and reports how long to wait', async () => {
      const clock = makeClock();
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        openDurationMs: 1_000,
        callTimeoutMs: null,
        now: clock.now,
      });
      await expect(breaker.execute(boom)).rejects.toThrow();

      const downstream = jest.fn(ok);
      clock.advance(400);

      await expect(breaker.execute(downstream)).rejects.toMatchObject({
        name: 'CircuitOpenError',
        retryAfterMs: 600,
      });
      expect(downstream).not.toHaveBeenCalled();
    });

    it('being refused by the breaker does not itself count as a failure', async () => {
      const clock = makeClock();
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        openDurationMs: 1_000,
        callTimeoutMs: null,
        now: clock.now,
      });
      await expect(breaker.execute(boom)).rejects.toThrow();
      const openedAt = breaker.snapshot().openedAt;

      clock.advance(100);
      await expect(breaker.execute(ok)).rejects.toBeInstanceOf(CircuitOpenError);

      // The open window must not be extended by traffic arriving while open.
      expect(breaker.snapshot().openedAt).toBe(openedAt);
    });
  });

  describe('HALF_OPEN', () => {
    it('admits a probe once the open window has elapsed', async () => {
      const clock = makeClock();
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        openDurationMs: 1_000,
        successThreshold: 2,
        callTimeoutMs: null,
        now: clock.now,
      });
      await expect(breaker.execute(boom)).rejects.toThrow();

      clock.advance(1_000);
      await expect(breaker.execute(ok)).resolves.toBe('ok');

      // One success, threshold is two — still probing.
      expect(breaker.getState()).toBe(CIRCUIT.HALF_OPEN);
    });

    it('admits only halfOpenMaxConcurrent probes at a time', async () => {
      const clock = makeClock();
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        openDurationMs: 1_000,
        halfOpenMaxConcurrent: 1,
        callTimeoutMs: null,
        now: clock.now,
      });
      await expect(breaker.execute(boom)).rejects.toThrow();
      clock.advance(1_000);

      const probe = deferred<string>();
      const inFlight = breaker.execute(() => probe.promise);
      const blocked = jest.fn(ok);

      // The permit is taken synchronously, so this second caller is refused
      // rather than piling onto a downstream that may still be unwell.
      await expect(breaker.execute(blocked)).rejects.toBeInstanceOf(CircuitOpenError);
      expect(blocked).not.toHaveBeenCalled();

      probe.resolve('ok');
      await expect(inFlight).resolves.toBe('ok');
    });

    it('closes after successThreshold consecutive probes succeed', async () => {
      const clock = makeClock();
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        openDurationMs: 1_000,
        successThreshold: 2,
        callTimeoutMs: null,
        now: clock.now,
      });
      await expect(breaker.execute(boom)).rejects.toThrow();
      clock.advance(1_000);

      await expect(breaker.execute(ok)).resolves.toBe('ok');
      await expect(breaker.execute(ok)).resolves.toBe('ok');

      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);
      expect(breaker.snapshot().consecutiveFailures).toBe(0);
      expect(breaker.snapshot().openedAt).toBeNull();
    });

    it('reopens on a single failed probe and restarts the open window', async () => {
      const clock = makeClock();
      const breaker = new CircuitBreaker({
        failureThreshold: 5,
        openDurationMs: 1_000,
        callTimeoutMs: null,
        now: clock.now,
      });

      for (let i = 0; i < 5; i += 1) {
        await expect(breaker.execute(boom)).rejects.toThrow();
      }
      expect(breaker.getState()).toBe(CIRCUIT.OPEN);

      clock.advance(1_000);
      // One failure is enough here, even though failureThreshold is 5.
      await expect(breaker.execute(boom)).rejects.toThrow();
      expect(breaker.getState()).toBe(CIRCUIT.OPEN);

      // The window restarted from the failed probe, not from the first trip.
      clock.advance(999);
      await expect(breaker.execute(ok)).rejects.toBeInstanceOf(CircuitOpenError);
      clock.advance(1);
      await expect(breaker.execute(ok)).resolves.toBe('ok');
    });

    it('returns the probe permit when the error is excluded by the policy', async () => {
      const clock = makeClock();
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        openDurationMs: 1_000,
        successThreshold: 2,
        callTimeoutMs: null,
        now: clock.now,
        isFailure: (error) => !(error instanceof RangeError),
      });
      await expect(breaker.execute(boom)).rejects.toThrow();
      clock.advance(1_000);

      await expect(breaker.execute(() => Promise.reject(new RangeError('bad input')))).rejects.toThrow('bad input');

      // Still half-open with its permit back — not deadlocked at zero.
      expect(breaker.getState()).toBe(CIRCUIT.HALF_OPEN);
      expect(breaker.snapshot().halfOpenPermits).toBe(1);
      await expect(breaker.execute(ok)).resolves.toBe('ok');
    });

    it('ignores a probe that lands after the circuit has moved on', async () => {
      const clock = makeClock();
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        openDurationMs: 1_000,
        callTimeoutMs: null,
        now: clock.now,
      });
      await expect(breaker.execute(boom)).rejects.toThrow();
      clock.advance(1_000);

      const probe = deferred<string>();
      const inFlight = breaker.execute(() => probe.promise);

      // An operator resets the breaker while the probe is still out.
      breaker.reset();
      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);

      probe.reject(new Error('too late'));
      await expect(inFlight).rejects.toThrow('too late');

      // The stale result belongs to a previous generation, so it must not
      // reopen the circuit the operator just closed.
      expect(breaker.getState()).toBe(CIRCUIT.CLOSED);
      expect(breaker.snapshot().consecutiveFailures).toBe(0);
    });
  });

  describe('timeouts', () => {
    it('treats a hung call as a failure', async () => {
      const breaker = new CircuitBreaker({ failureThreshold: 1, callTimeoutMs: 20 });

      await expect(breaker.execute(() => new Promise(() => {}))).rejects.toBeInstanceOf(CallTimeoutError);

      expect(breaker.getState()).toBe(CIRCUIT.OPEN);
    });
  });

  describe('observability', () => {
    it('reports every transition', async () => {
      const clock = makeClock();
      const changes: StateChange[] = [];
      const breaker = new CircuitBreaker({
        failureThreshold: 1,
        successThreshold: 1,
        openDurationMs: 1_000,
        callTimeoutMs: null,
        now: clock.now,
        onStateChange: (change) => changes.push(change),
      });

      await expect(breaker.execute(boom)).rejects.toThrow();
      clock.advance(1_000);
      await expect(breaker.execute(ok)).resolves.toBe('ok');

      expect(changes.map((c) => `${c.from}->${c.to}`)).toEqual([
        'CLOSED->OPEN',
        'OPEN->HALF_OPEN',
        'HALF_OPEN->CLOSED',
      ]);
      expect(changes[0].at).toBe(0);
      expect(changes[1].at).toBe(1_000);
    });
  });
});
