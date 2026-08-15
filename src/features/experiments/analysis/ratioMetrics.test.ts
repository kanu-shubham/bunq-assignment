import { compareRatioMetric, estimateRatio, RatioUnit } from './ratioMetrics';
import { twoProportionZTest } from './stats';

/** Deterministic LCG so the simulated traffic is reproducible. */
function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/**
 * Realistic traffic: request counts are skewed (10% of users issue ~50 requests,
 * the rest a handful) AND users differ in how click-happy they are.
 *
 * Both halves matter. Volume skew alone does not break the naive test — with a
 * single shared click rate, requests really are independent Bernoulli draws and
 * the delta-method SE agrees with the binomial one. What breaks it is
 * *heterogeneity*: a user with a high personal rate contributes a run of
 * correlated clicks, so each extra request from that user carries less
 * information than the naive test assumes.
 */
function arm(users: number, clickRate: number, seed: number): RatioUnit[] {
  const next = rng(seed);
  const out: RatioUnit[] = [];
  for (let i = 0; i < users; i += 1) {
    const heavy = next() < 0.1;
    const impressions = heavy ? 40 + Math.floor(next() * 20) : 1 + Math.floor(next() * 3);
    // Per-user propensity: half the users barely click, some click constantly.
    const userRate = Math.min(1, Math.max(0, clickRate * (next() < 0.5 ? 0.2 : 1.8)));
    let clicks = 0;
    for (let k = 0; k < impressions; k += 1) if (next() < userRate) clicks += 1;
    out.push({ numerator: clicks, denominator: impressions });
  }
  return out;
}

describe('estimateRatio', () => {
  test('computes the pooled ratio, not the mean of per-user ratios', () => {
    // 1 click / 10 impressions and 1 click / 1 impression.
    // Pooled = 2/11 = 0.1818; the mean of user ratios would be 0.55.
    const { ratio } = estimateRatio([
      { numerator: 1, denominator: 10 },
      { numerator: 1, denominator: 1 },
    ]);
    expect(ratio).toBeCloseTo(2 / 11, 9);
  });

  test('zero denominators do not produce NaN', () => {
    const est = estimateRatio([
      { numerator: 0, denominator: 0 },
      { numerator: 1, denominator: 4 },
    ]);
    expect(Number.isFinite(est.ratio)).toBe(true);
    expect(Number.isFinite(est.standardError)).toBe(true);
  });

  test('rejects an empty arm', () => {
    expect(() => estimateRatio([])).toThrow(RangeError);
  });
});

describe('compareRatioMetric', () => {
  test('clustered SE is much larger than the naive per-request SE', () => {
    const control = arm(4_000, 0.1, 7);
    const treatment = arm(4_000, 0.1, 99);
    const result = compareRatioMetric(control, treatment);

    // This factor IS the bug: on this traffic shape the naive per-request test
    // understates the standard error by ~1.95x, so it reports a z-score about
    // twice as large as the data supports.
    expect(result.clusteringInflation).toBeGreaterThan(1.8);
  });

  test('the naive test invents significance where the clustered test does not', () => {
    const control = arm(4_000, 0.1, 7);
    const treatment = arm(4_000, 0.1, 99); // same true rate — any "win" is noise
    const clustered = compareRatioMetric(control, treatment);

    const totals = (units: RatioUnit[]) => ({
      units: units.reduce((a, u) => a + u.denominator, 0),
      conversions: units.reduce((a, u) => a + u.numerator, 0),
    });
    const naive = twoProportionZTest(totals(control), totals(treatment));

    expect(Math.abs(naive.zScore)).toBeGreaterThan(Math.abs(clustered.zScore));
    expect(clustered.significant).toBe(false);
  });

  test('still detects a real effect', () => {
    const result = compareRatioMetric(arm(6_000, 0.1, 3), arm(6_000, 0.13, 4));
    expect(result.significant).toBe(true);
    expect(result.absoluteLift).toBeGreaterThan(0.015);
    expect(result.confidenceInterval[0]).toBeGreaterThan(0);
  });

  test('reports absolute and relative lift consistently', () => {
    const result = compareRatioMetric(arm(2_000, 0.1, 11), arm(2_000, 0.12, 12));
    expect(result.relativeLift).toBeCloseTo(result.absoluteLift / result.control.ratio, 9);
  });

  test('identical arms straddle zero', () => {
    const units = arm(1_000, 0.1, 21);
    const result = compareRatioMetric(units, units);
    expect(result.absoluteLift).toBe(0);
    expect(result.confidenceInterval[0]).toBeLessThan(0);
    expect(result.confidenceInterval[1]).toBeGreaterThan(0);
  });
});
