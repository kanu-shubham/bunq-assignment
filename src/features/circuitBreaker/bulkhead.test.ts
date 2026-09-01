import { Bulkhead, BulkheadFullError } from './bulkhead';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('Bulkhead', () => {
  it('caps how many calls run at once and queues the rest', async () => {
    const bulkhead = new Bulkhead({ maxConcurrent: 2, maxQueued: 5 });
    const gates = [deferred<string>(), deferred<string>(), deferred<string>()];
    const started: number[] = [];

    const calls = gates.map((gate, i) =>
      bulkhead.execute(() => {
        started.push(i);
        return gate.promise;
      }),
    );

    await Promise.resolve();
    expect(started).toEqual([0, 1]);
    expect(bulkhead.snapshot()).toEqual({ inFlight: 2, queued: 1 });

    gates[0].resolve('a');
    await calls[0];
    expect(started).toEqual([0, 1, 2]);

    gates[1].resolve('b');
    gates[2].resolve('c');
    await expect(Promise.all(calls)).resolves.toEqual(['a', 'b', 'c']);
    expect(bulkhead.snapshot()).toEqual({ inFlight: 0, queued: 0 });
  });

  it('rejects once the queue is full rather than growing without bound', async () => {
    const bulkhead = new Bulkhead({ maxConcurrent: 1, maxQueued: 1 });
    const gate = deferred<string>();

    const running = bulkhead.execute(() => gate.promise);
    const queued = bulkhead.execute(() => Promise.resolve('queued'));

    // An unbounded queue would just turn a saturated pool into an OOM.
    await expect(bulkhead.execute(() => Promise.resolve('rejected'))).rejects.toBeInstanceOf(BulkheadFullError);

    gate.resolve('running');
    await expect(running).resolves.toBe('running');
    await expect(queued).resolves.toBe('queued');
  });

  it('frees its slot when the call throws', async () => {
    const bulkhead = new Bulkhead({ maxConcurrent: 1, maxQueued: 0 });

    await expect(bulkhead.execute(() => Promise.reject(new Error('boom')))).rejects.toThrow('boom');

    expect(bulkhead.snapshot()).toEqual({ inFlight: 0, queued: 0 });
    await expect(bulkhead.execute(() => Promise.resolve('ok'))).resolves.toBe('ok');
  });
});
