import { CircuitOpen, MiniBreaker, MiniWebClient } from './minimal';

function makeClock(start = 0) {
  let t = start;
  return { now: () => t, advance: (ms: number) => { t += ms; } };
}

const ok = () => Promise.resolve('ok');
const boom = () => Promise.reject(new Error('down'));

describe('MiniBreaker (the interview-length version)', () => {
  it('opens, refuses, probes, and closes', async () => {
    const clock = makeClock();
    const breaker = new MiniBreaker(2, 1, 1_000, clock.now);

    await expect(breaker.execute(boom)).rejects.toThrow('down');
    await expect(breaker.execute(boom)).rejects.toThrow('down');
    expect(breaker.getState()).toBe('OPEN');

    const skipped = jest.fn(ok);
    await expect(breaker.execute(skipped)).rejects.toBeInstanceOf(CircuitOpen);
    expect(skipped).not.toHaveBeenCalled();

    clock.advance(1_000);
    await expect(breaker.execute(ok)).resolves.toBe('ok');
    expect(breaker.getState()).toBe('CLOSED');
  });

  it('admits one probe at a time', async () => {
    const clock = makeClock();
    const breaker = new MiniBreaker(1, 1, 1_000, clock.now);
    await expect(breaker.execute(boom)).rejects.toThrow();
    clock.advance(1_000);

    let release!: (v: string) => void;
    const gate = new Promise<string>((res) => { release = res; });
    const probe = breaker.execute(() => gate);

    await expect(breaker.execute(ok)).rejects.toBeInstanceOf(CircuitOpen);

    release('ok');
    await expect(probe).resolves.toBe('ok');
  });

  it('reopens on a failed probe', async () => {
    const clock = makeClock();
    const breaker = new MiniBreaker(1, 1, 1_000, clock.now);

    await expect(breaker.execute(boom)).rejects.toThrow();
    clock.advance(1_000);
    await expect(breaker.execute(boom)).rejects.toThrow();

    expect(breaker.getState()).toBe('OPEN');
    await expect(breaker.execute(ok)).rejects.toBeInstanceOf(CircuitOpen);
  });
});

describe('MiniWebClient', () => {
  it('caches, then serves stale once the downstream fails', async () => {
    const clock = makeClock();
    const transport = jest
      .fn<Promise<string>, []>()
      .mockResolvedValueOnce('balance')
      .mockRejectedValue(new Error('down'));
    const client = new MiniWebClient(transport, new MiniBreaker(1, 1, 1_000, clock.now), 100, clock.now);

    expect(await client.execute({ url: '/balances' })).toEqual({ value: 'balance', stale: false });

    clock.advance(50);
    expect(await client.execute({ url: '/balances' })).toEqual({ value: 'balance', stale: false });
    expect(transport).toHaveBeenCalledTimes(1);

    clock.advance(100); // now stale, so it tries the network and fails
    expect(await client.execute({ url: '/balances' })).toEqual({ value: 'balance', stale: true });
  });

  it('does not cache a POST', async () => {
    const transport = jest.fn<Promise<string>, []>().mockResolvedValue('created');
    const client = new MiniWebClient(transport, new MiniBreaker());

    await client.execute({ url: '/transfers', method: 'POST' });
    await client.execute({ url: '/transfers', method: 'POST' });

    expect(transport).toHaveBeenCalledTimes(2);
  });

  it('rethrows when there is nothing cached to fall back to', async () => {
    const transport = jest.fn<Promise<string>, []>().mockRejectedValue(new Error('down'));
    const client = new MiniWebClient(transport, new MiniBreaker());

    await expect(client.execute({ url: '/balances' })).rejects.toThrow('down');
  });
});
