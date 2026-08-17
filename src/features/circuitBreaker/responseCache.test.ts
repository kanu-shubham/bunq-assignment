import { ResponseCache } from './responseCache';

function makeClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    advance: (ms: number) => {
      t += ms;
    },
  };
}

describe('ResponseCache', () => {
  it('serves an entry as fresh inside its TTL', () => {
    const clock = makeClock();
    const cache = new ResponseCache<string>({ ttlMs: 100, now: clock.now });

    cache.set('a', 'value');
    clock.advance(99);

    expect(cache.get('a')).toEqual({ value: 'value', ageMs: 99, isFresh: true });
  });

  it('keeps the entry past its TTL, flagged as stale', () => {
    const clock = makeClock();
    const cache = new ResponseCache<string>({ ttlMs: 100, now: clock.now });

    cache.set('a', 'value');
    clock.advance(5_000);

    // Retaining this is what lets the client fail static when the circuit opens.
    expect(cache.get('a')).toEqual({ value: 'value', ageMs: 5_000, isFresh: false });
  });

  it('returns undefined for an unknown key', () => {
    expect(new ResponseCache<string>().get('nope')).toBeUndefined();
  });

  it('evicts least-recently-used entries once full', () => {
    const cache = new ResponseCache<string>({ maxEntries: 2 });

    cache.set('a', '1');
    cache.set('b', '2');
    cache.get('a'); // 'a' is now the most recently used, so 'b' is the victim
    cache.set('c', '3');

    expect(cache.size).toBe(2);
    expect(cache.get('a')?.value).toBe('1');
    expect(cache.get('c')?.value).toBe('3');
    expect(cache.get('b')).toBeUndefined();
  });

  it('overwrites rather than duplicating on re-set', () => {
    const clock = makeClock();
    const cache = new ResponseCache<string>({ ttlMs: 100, now: clock.now });

    cache.set('a', 'old');
    clock.advance(50);
    cache.set('a', 'new');

    expect(cache.size).toBe(1);
    expect(cache.get('a')).toEqual({ value: 'new', ageMs: 0, isFresh: true });
  });

  it('supports explicit invalidation', () => {
    const cache = new ResponseCache<string>();

    cache.set('a', '1');
    cache.set('b', '2');
    cache.delete('a');
    expect(cache.get('a')).toBeUndefined();
    expect(cache.size).toBe(1);

    cache.clear();
    expect(cache.size).toBe(0);
  });
});
