import { normalCdf, normalQuantile } from './stats';

/**
 * Ratio metrics with the randomisation unit as the cluster.
 *
 * Almost every metric an ML engineer cares about is a ratio whose denominator
 * is itself random: click-through rate (clicks / impressions), precision at k,
 * watch time per session, fraud catch rate per transaction. You randomise
 * *users*, but the metric is computed per *request*.
 *
 * Running a naive per-request z-test on that is the most common statistical bug
 * in ML experimentation, and it fails in one direction only: one user's 500
 * requests are not 500 independent observations, so the naive standard error is
 * too small, everything looks significant, and half your "wins" are noise. The
 * fix is the delta method applied at the user level:
 *
 *     R = Σnᵢ / Σdᵢ    (i indexes users, not requests)
 *     Var(R) ≈ ( Var(nᵢ) + R²·Var(dᵢ) − 2R·Cov(nᵢ, dᵢ) ) / (m · d̄²)
 *
 * m is the number of *users*. The covariance term is what the naive analysis
 * throws away, and it's usually large: users with more impressions also have
 * more clicks.
 */

export interface RatioUnit {
  /** Per-user numerator total, e.g. clicks. */
  numerator: number;
  /** Per-user denominator total, e.g. impressions. Zero is allowed. */
  denominator: number;
}

export interface RatioEstimate {
  /** Σnumerator / Σdenominator across the arm. */
  ratio: number;
  /** Delta-method variance of that ratio, clustered by unit. */
  variance: number;
  standardError: number;
  units: number;
  denominatorTotal: number;
}

const mean = (xs: ReadonlyArray<number>): number =>
  xs.length === 0 ? 0 : xs.reduce((a, b) => a + b, 0) / xs.length;

export function estimateRatio(units: ReadonlyArray<RatioUnit>): RatioEstimate {
  const m = units.length;
  if (m === 0) throw new RangeError('estimateRatio needs at least one unit');

  const numerators = units.map((u) => u.numerator);
  const denominators = units.map((u) => u.denominator);
  const nBar = mean(numerators);
  const dBar = mean(denominators);
  if (dBar === 0) {
    return { ratio: 0, variance: 0, standardError: 0, units: m, denominatorTotal: 0 };
  }
  const ratio = nBar / dBar;

  let varN = 0;
  let varD = 0;
  let covND = 0;
  for (let i = 0; i < m; i += 1) {
    const dn = numerators[i] - nBar;
    const dd = denominators[i] - dBar;
    varN += dn * dn;
    varD += dd * dd;
    covND += dn * dd;
  }
  const denom = m - 1 || 1;
  varN /= denom;
  varD /= denom;
  covND /= denom;

  const variance = (varN + ratio * ratio * varD - 2 * ratio * covND) / (m * dBar * dBar);
  return {
    ratio,
    variance: Math.max(0, variance),
    standardError: Math.sqrt(Math.max(0, variance)),
    units: m,
    denominatorTotal: denominators.reduce((a, b) => a + b, 0),
  };
}

export interface RatioComparison {
  control: RatioEstimate;
  treatment: RatioEstimate;
  absoluteLift: number;
  relativeLift: number;
  standardError: number;
  zScore: number;
  pValue: number;
  confidenceInterval: [number, number];
  significant: boolean;
  /**
   * How much the naive per-request analysis would have understated the standard
   * error. Values above ~1.5 mean a naive test is inventing significance.
   */
  clusteringInflation: number;
}

/** Two-arm comparison of a clustered ratio metric. */
export function compareRatioMetric(
  control: ReadonlyArray<RatioUnit>,
  treatment: ReadonlyArray<RatioUnit>,
  alpha = 0.05,
): RatioComparison {
  const c = estimateRatio(control);
  const t = estimateRatio(treatment);

  const standardError = Math.sqrt(c.variance + t.variance);
  const diff = t.ratio - c.ratio;
  const zScore = standardError === 0 ? 0 : diff / standardError;
  const pValue = 2 * (1 - normalCdf(Math.abs(zScore)));
  const zCritical = normalQuantile(1 - alpha / 2);

  // What a per-request binomial test would have claimed, for comparison.
  const naiveSe = Math.sqrt(
    (c.ratio * (1 - c.ratio)) / Math.max(1, c.denominatorTotal) +
      (t.ratio * (1 - t.ratio)) / Math.max(1, t.denominatorTotal),
  );

  return {
    control: c,
    treatment: t,
    absoluteLift: diff,
    relativeLift: c.ratio === 0 ? Infinity : diff / c.ratio,
    standardError,
    zScore,
    pValue,
    confidenceInterval: [diff - zCritical * standardError, diff + zCritical * standardError],
    significant: pValue < alpha,
    clusteringInflation: naiveSe === 0 ? 1 : standardError / naiveSe,
  };
}
