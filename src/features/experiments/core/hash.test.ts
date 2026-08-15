import { fnv1a32, hashFraction, trafficBucket, variantBucket } from './hash';

const ids = (n: number, prefix = 'user'): string[] =>
  Array.from({ length: n }, (_, i) => `${prefix}-${i}`);

describe('fnv1a32', () => {
  test('matches the reference vectors', () => {
    // From the FNV spec — proof we implemented FNV-1a and not something adjacent.
    expect(fnv1a32('')).toBe(0x811c9dc5);
    expect(fnv1a32('a')).toBe(0xe40c292c);
    expect(fnv1a32('foobar')).toBe(0xbf9cf968);
  });

  test('stays inside uint32', () => {
    for (const id of ids(500)) {
      const h = fnv1a32(id);
      expect(Number.isInteger(h)).toBe(true);
      expect(h).toBeGreaterThanOrEqual(0);
      expect(h).toBeLessThan(2 ** 32);
    }
  });
});

describe('hashFraction', () => {
  test('is deterministic', () => {
    expect(hashFraction('exp', 'user-1')).toBe(hashFraction('exp', 'user-1'));
  });

  test('lands in [0, 1)', () => {
    for (const id of ids(1000)) {
      const f = hashFraction('exp', id);
      expect(f).toBeGreaterThanOrEqual(0);
      expect(f).toBeLessThan(1);
    }
  });

  test('separator prevents concatenation collisions', () => {
    expect(hashFraction('ab', 'cd')).not.toBe(hashFraction('a', 'bcd'));
  });

  test('is uniform across 10 buckets (±4σ)', () => {
    const n = 20_000;
    const counts = new Array(10).fill(0);
    for (const id of ids(n)) counts[Math.floor(hashFraction('checkout-cta', id) * 10)] += 1;

    // σ = sqrt(n·p·(1−p)) ≈ 42.4 for p = 0.1; 4σ ≈ 170.
    for (const count of counts) {
      expect(count).toBeGreaterThan(n / 10 - 170);
      expect(count).toBeLessThan(n / 10 + 170);
    }
  });

  test('different salts randomise independently', () => {
    const n = 20_000;
    let bothTreatment = 0;
    for (const id of ids(n)) {
      if (hashFraction('experiment-a', id) < 0.5 && hashFraction('experiment-b', id) < 0.5) {
        bothTreatment += 1;
      }
    }
    // Independent ⇒ ≈ 25%. Correlated salts would land near 50% (or near 0%).
    expect(bothTreatment / n).toBeGreaterThan(0.235);
    expect(bothTreatment / n).toBeLessThan(0.265);
  });
});

test('traffic and variant draws are independent of each other', () => {
  const n = 10_000;
  let agree = 0;
  for (const id of ids(n)) {
    if ((trafficBucket('exp', id) < 0.5) === (variantBucket('exp', id) < 0.5)) agree += 1;
  }
  // A shared draw would agree 100% of the time.
  expect(agree / n).toBeGreaterThan(0.47);
  expect(agree / n).toBeLessThan(0.53);
});
