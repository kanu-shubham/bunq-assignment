import { BulkheadFullError } from './bulkhead';
import { CircuitOpenError } from './circuitBreaker';
import { isRetryableError, retry } from './retry';

/** Records the delays instead of waiting them out. */
function recordingSleep() {
  const delays: number[] = [];
  return {
    delays,
    sleep: (ms: number) => {
      delays.push(ms);
      return Promise.resolve();
    },
  };
}

describe('retry', () => {
  it('returns the first success without sleeping', async () => {
    const { delays, sleep } = recordingSleep();
    const fn = jest.fn<Promise<string>, []>().mockResolvedValue('ok');

    await expect(retry(fn, { sleep })).resolves.toBe('ok');

    expect(fn).toHaveBeenCalledTimes(1);
    expect(delays).toEqual([]);
  });

  it('retries up to maxAttempts and then rethrows the last error', async () => {
    const { sleep } = recordingSleep();
    const fn = jest.fn<Promise<string>, []>().mockRejectedValue(new Error('down'));

    await expect(retry(fn, { maxAttempts: 3, sleep })).rejects.toThrow('down');

    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('backs off exponentially with full jitter', async () => {
    const { delays, sleep } = recordingSleep();
    const fn = jest.fn<Promise<string>, []>().mockRejectedValue(new Error('down'));

    // random() pinned to 1 so we observe the ceiling of each jitter range.
    await expect(
      retry(fn, { maxAttempts: 4, baseDelayMs: 100, maxDelayMs: 300, random: () => 1, sleep }),
    ).rejects.toThrow('down');

    // 100, 200, then capped at 300 rather than 400.
    expect(delays).toEqual([100, 200, 300]);
  });

  it('picks uniformly inside the window, so a fleet does not retry in lockstep', async () => {
    const { delays, sleep } = recordingSleep();
    const fn = jest.fn<Promise<string>, []>().mockRejectedValue(new Error('down'));

    await expect(
      retry(fn, { maxAttempts: 3, baseDelayMs: 100, random: () => 0.25, sleep }),
    ).rejects.toThrow('down');

    expect(delays).toEqual([25, 50]);
  });

  it('does not retry into an open circuit', async () => {
    const { delays, sleep } = recordingSleep();
    const fn = jest.fn<Promise<string>, []>().mockRejectedValue(new CircuitOpenError(5_000));

    await expect(retry(fn, { maxAttempts: 5, sleep })).rejects.toBeInstanceOf(CircuitOpenError);

    // The call never reached the downstream, so trying again cannot help —
    // fail in microseconds instead of sleeping through the backoff ladder.
    expect(fn).toHaveBeenCalledTimes(1);
    expect(delays).toEqual([]);
  });

  it('does not retry a full bulkhead either', async () => {
    expect(isRetryableError(new BulkheadFullError(1, 1))).toBe(false);
    expect(isRetryableError(new Error('connection reset'))).toBe(true);
  });

  it('stops early on a non-retryable error', async () => {
    const { sleep } = recordingSleep();
    const fn = jest.fn<Promise<string>, []>().mockRejectedValue(new RangeError('bad input'));

    await expect(
      retry(fn, { maxAttempts: 5, sleep, isRetryable: (e) => !(e instanceof RangeError) }),
    ).rejects.toThrow('bad input');

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('succeeds on a later attempt', async () => {
    const { sleep } = recordingSleep();
    const fn = jest
      .fn<Promise<string>, []>()
      .mockRejectedValueOnce(new Error('flaky'))
      .mockResolvedValue('ok');

    await expect(retry(fn, { sleep })).resolves.toBe('ok');
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
