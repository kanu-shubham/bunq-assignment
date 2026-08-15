import { normalCdf, normalQuantile } from './stats';

/**
 * CUPED — Controlled-experiment Using Pre-Experiment Data (Deng et al., 2013).
 *
 * The staff-level lever when the answer to "we don't have enough traffic" has
 * to be something other than "wait longer". Instead of comparing raw metric Y,
 * compare
 *
 *     Y' = Y − θ·(X − X̄)
 *
 * where X is a covariate measured *before* the experiment started (last month's
 * spend, sessions in the prior week, pre-period value of the same metric).
 * Because X is pre-treatment it cannot be affected by the variant, so Y' has
 * the same expected difference between arms — it is unbiased — but variance
 * Var(Y)·(1 − ρ²). With ρ = 0.7 that is half the variance, i.e. the same
 * sensitivity in half the traffic.
 *
 * The catch, and the reason this is a review item and not a default: X must be
 * strictly pre-treatment. A covariate contaminated by the experiment (a
 * "pre-period" window that overlaps the ramp, a covariate computed from
 * post-exposure sessions) bakes the treatment effect into the correction and
 * quietly biases the result.
 */

export interface Moments {
  mean: number;
  variance: number;
}

export function moments(values: ReadonlyArray<number>): Moments {
  const n = values.length;
  if (n === 0) return { mean: 0, variance: 0 };
  const mean = values.reduce((a, b) => a + b, 0) / n;
  if (n < 2) return { mean, variance: 0 };
  const variance = values.reduce((acc, v) => acc + (v - mean) * (v - mean), 0) / (n - 1);
  return { mean, variance };
}

export function covariance(xs: ReadonlyArray<number>, ys: ReadonlyArray<number>): number {
  const n = Math.min(xs.length, ys.length);
  if (n < 2) return 0;
  const xBar = moments(xs.slice(0, n)).mean;
  const yBar = moments(ys.slice(0, n)).mean;
  let sum = 0;
  for (let i = 0; i < n; i += 1) sum += (xs[i] - xBar) * (ys[i] - yBar);
  return sum / (n - 1);
}

/** θ = Cov(Y, X) / Var(X), estimated on the *pooled* arms so it stays unbiased. */
export function cupedTheta(metric: ReadonlyArray<number>, covariate: ReadonlyArray<number>): number {
  const varX = moments(covariate).variance;
  return varX === 0 ? 0 : covariance(covariate, metric) / varX;
}

export interface CupedArm {
  metric: ReadonlyArray<number>;
  covariate: ReadonlyArray<number>;
}

export interface CupedResult {
  theta: number;
  /** Pearson ρ between metric and covariate across both arms. */
  correlation: number;
  /** Share of variance removed, 0..1. Equals ρ². */
  varianceReduction: number;
  difference: number;
  standardError: number;
  zScore: number;
  pValue: number;
  confidenceInterval: [number, number];
  significant: boolean;
}

/**
 * Run the adjusted comparison of two arms on a continuous metric.
 * Welch-style standard error (arms are not assumed to share a variance),
 * normal approximation — sample sizes in an online experiment are large enough
 * that the t-distribution correction is invisible.
 */
export function cupedCompare(
  control: CupedArm,
  treatment: CupedArm,
  alpha = 0.05,
): CupedResult {
  const pooledMetric = [...control.metric, ...treatment.metric];
  const pooledCovariate = [...control.covariate, ...treatment.covariate];
  if (pooledMetric.length !== pooledCovariate.length) {
    throw new RangeError('each unit needs exactly one covariate value');
  }

  const theta = cupedTheta(pooledMetric, pooledCovariate);
  const covariateMean = moments(pooledCovariate).mean;
  const adjust = (arm: CupedArm): number[] =>
    arm.metric.map((y, i) => y - theta * (arm.covariate[i] - covariateMean));

  const a = adjust(control);
  const b = adjust(treatment);
  const ma = moments(a);
  const mb = moments(b);

  const standardError = Math.sqrt(ma.variance / a.length + mb.variance / b.length);
  const difference = mb.mean - ma.mean;
  const zScore = standardError === 0 ? 0 : difference / standardError;
  const pValue = 2 * (1 - normalCdf(Math.abs(zScore)));
  const zCritical = normalQuantile(1 - alpha / 2);

  const rawVariance = moments(pooledMetric).variance;
  const adjustedVariance = moments([...a, ...b]).variance;
  const varianceReduction =
    rawVariance === 0 ? 0 : Math.max(0, 1 - adjustedVariance / rawVariance);

  return {
    theta,
    correlation: Math.sqrt(varianceReduction) * Math.sign(theta || 1),
    varianceReduction,
    difference,
    standardError,
    zScore,
    pValue,
    confidenceInterval: [
      difference - zCritical * standardError,
      difference + zCritical * standardError,
    ],
    significant: pValue < alpha,
  };
}
