import { covariance, cupedCompare, cupedTheta, moments } from './cuped';

/** Deterministic LCG — a seeded generator keeps the assertions reproducible. */
function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/** Box–Muller on top of the LCG. */
function normal(next: () => number): number {
  const u1 = Math.max(next(), 1e-12);
  const u2 = next();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

interface Arm {
  metric: number[];
  covariate: number[];
}

/**
 * Simulate an arm where the covariate (last month's value of the metric)
 * explains most of the metric's variance — the situation CUPED was built for.
 */
function arm(n: number, effect: number, seed: number, rho = 0.9): Arm {
  const next = rng(seed);
  const metric: number[] = [];
  const covariate: number[] = [];
  for (let i = 0; i < n; i += 1) {
    const base = normal(next);
    covariate.push(base);
    metric.push(rho * base + Math.sqrt(1 - rho * rho) * normal(next) + effect);
  }
  return { metric, covariate };
}

describe('moments / covariance', () => {
  test('mean and sample variance', () => {
    const { mean, variance } = moments([2, 4, 4, 4, 5, 5, 7, 9]);
    expect(mean).toBe(5);
    expect(variance).toBeCloseTo(4.571, 3); // n−1 denominator
  });

  test('degenerate inputs do not blow up', () => {
    expect(moments([])).toEqual({ mean: 0, variance: 0 });
    expect(moments([3])).toEqual({ mean: 3, variance: 0 });
    expect(covariance([1], [1])).toBe(0);
  });

  test('covariance is symmetric and signed', () => {
    const xs = [1, 2, 3, 4];
    const ys = [2, 4, 6, 8];
    expect(covariance(xs, ys)).toBeCloseTo(covariance(ys, xs), 12);
    expect(covariance(xs, [8, 6, 4, 2])).toBeLessThan(0);
  });
});

describe('cupedTheta', () => {
  test('recovers the slope of the metric on the covariate', () => {
    const covariate = [1, 2, 3, 4, 5];
    const metric = covariate.map((x) => 3 * x + 1);
    expect(cupedTheta(metric, covariate)).toBeCloseTo(3, 9);
  });

  test('is zero when the covariate carries no information', () => {
    expect(cupedTheta([1, 5, 2, 8], [7, 7, 7, 7])).toBe(0);
  });
});

describe('cupedCompare', () => {
  const control = arm(4000, 0, 12345);
  const treatment = arm(4000, 0.05, 67890);

  test('removes most of the variance when ρ is high', () => {
    const result = cupedCompare(control, treatment);
    expect(result.varianceReduction).toBeGreaterThan(0.7); // ρ² ≈ 0.81
    expect(result.theta).toBeCloseTo(0.9, 1);
  });

  test('keeps the effect estimate unbiased', () => {
    const result = cupedCompare(control, treatment);
    expect(result.difference).toBeCloseTo(0.05, 1);
    expect(result.confidenceInterval[0]).toBeLessThan(0.05);
    expect(result.confidenceInterval[1]).toBeGreaterThan(0.05);
  });

  test('is more sensitive than the unadjusted comparison', () => {
    const adjusted = cupedCompare(control, treatment);
    // Same arms, covariate stripped of its signal ⇒ no adjustment possible.
    const flat = { covariate: control.metric.map(() => 0) };
    const unadjusted = cupedCompare(
      { metric: control.metric, ...flat },
      { metric: treatment.metric, covariate: treatment.metric.map(() => 0) },
    );
    expect(adjusted.standardError).toBeLessThan(unadjusted.standardError);
    expect(adjusted.pValue).toBeLessThan(unadjusted.pValue);
  });

  test('finds nothing when there is nothing to find', () => {
    const a = arm(3000, 0, 111);
    const b = arm(3000, 0, 222);
    expect(cupedCompare(a, b).significant).toBe(false);
  });

  test('rejects unpaired inputs', () => {
    expect(() =>
      cupedCompare({ metric: [1, 2], covariate: [1] }, { metric: [1], covariate: [1] }),
    ).toThrow(RangeError);
  });
});
