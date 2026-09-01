import { CIRCUIT, CircuitBreaker } from './circuitBreaker';
import { ConsecutiveFailurePolicy, SlidingWindowFailurePolicy } from './failurePolicy';

describe('ConsecutiveFailurePolicy', () => {
  it('opens only on an unbroken run', () => {
    const policy = new ConsecutiveFailurePolicy(3);

    policy.recordFailure();
    policy.recordFailure();
    policy.recordSuccess();
    policy.recordFailure();
    policy.recordFailure();

    expect(policy.shouldOpen()).toBe(false);
    policy.recordFailure();
    expect(policy.shouldOpen()).toBe(true);
  });
});

describe('SlidingWindowFailurePolicy', () => {
  const record = (policy: SlidingWindowFailurePolicy, pattern: string) => {
    for (const c of pattern) {
      if (c === 'x') policy.recordFailure();
      else policy.recordSuccess();
    }
  };

  it('waits for minimumCalls before judging', () => {
    const policy = new SlidingWindowFailurePolicy({ windowSize: 10, failureRateThreshold: 0.5, minimumCalls: 5 });

    record(policy, 'xxxx'); // 100% failure, but only 4 calls of evidence
    expect(policy.shouldOpen()).toBe(false);

    record(policy, 'x');
    expect(policy.shouldOpen()).toBe(true);
  });

  it('opens on failure *rate* where a consecutive counter never would', () => {
    const consecutive = new ConsecutiveFailurePolicy(5);
    const window = new SlidingWindowFailurePolicy({ windowSize: 10, failureRateThreshold: 0.5, minimumCalls: 10 });

    // Alternating: a 50% error rate that never produces a run of five.
    const pattern = 'xoxoxoxoxo';
    record(window, pattern);
    for (const c of pattern) {
      if (c === 'x') consecutive.recordFailure();
      else consecutive.recordSuccess();
    }

    expect(consecutive.shouldOpen()).toBe(false);
    expect(window.shouldOpen()).toBe(true);
  });

  it('forgets outcomes that slide out of the window', () => {
    const policy = new SlidingWindowFailurePolicy({ windowSize: 4, failureRateThreshold: 0.5, minimumCalls: 4 });

    record(policy, 'xxxx');
    expect(policy.snapshot()).toEqual({ calls: 4, failures: 4, failureRate: 1 });

    record(policy, 'oooo'); // pushes every failure out of the ring buffer
    expect(policy.snapshot()).toEqual({ calls: 4, failures: 0, failureRate: 0 });
    expect(policy.shouldOpen()).toBe(false);
  });

  it('plugs into the breaker without the breaker knowing', async () => {
    const breaker = new CircuitBreaker({
      callTimeoutMs: null,
      failurePolicy: new SlidingWindowFailurePolicy({
        windowSize: 4,
        failureRateThreshold: 0.5,
        minimumCalls: 4,
      }),
    });

    const ok = () => Promise.resolve('ok');
    const boom = () => Promise.reject(new Error('nope'));

    await expect(breaker.execute(ok)).resolves.toBe('ok');
    await expect(breaker.execute(boom)).rejects.toThrow();
    await expect(breaker.execute(ok)).resolves.toBe('ok');
    expect(breaker.getState()).toBe(CIRCUIT.CLOSED);

    // Fourth call takes the window to 2/4 = 50%.
    await expect(breaker.execute(boom)).rejects.toThrow();
    expect(breaker.getState()).toBe(CIRCUIT.OPEN);
  });
});
