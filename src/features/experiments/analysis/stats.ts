/**
 * The analysis half of an experiment. Shipping a framework that only *assigns*
 * variants is shipping half a product: without these functions nobody can say
 * how long to run, whether the split was honest, or whether the win is real.
 *
 * Everything here assumes a two-arm, fixed-horizon, binary-metric test
 * (conversion rate), because that is the 90% case and its failure modes are
 * the ones asked about in interviews. Where the assumption bites — peeking,
 * ratio metrics, clustered units — the doc says so explicitly.
 */

/* ------------------------------------------------------------------ *
 * Normal / gamma primitives (no dependencies, ~1e-7 accurate)
 * ------------------------------------------------------------------ */

/** Complementary error function. Numerical Recipes 6.2, |ε| < 1.2e-7. */
export function erfc(x: number): number {
  const z = Math.abs(x);
  const t = 1 / (1 + z / 2);
  const ans =
    t *
    Math.exp(
      -z * z -
        1.26551223 +
        t *
          (1.00002368 +
            t *
              (0.37409196 +
                t *
                  (0.09678418 +
                    t *
                      (-0.18628806 +
                        t *
                          (0.27886807 +
                            t *
                              (-1.13520398 +
                                t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))),
    );
  return x >= 0 ? ans : 2 - ans;
}

/** Φ(z): P(Z ≤ z) for a standard normal. */
export const normalCdf = (z: number): number => 0.5 * erfc(-z / Math.SQRT2);

/** Φ⁻¹(p). Acklam's rational approximation, |ε| < 1.2e-9. */
export function normalQuantile(p: number): number {
  if (p <= 0 || p >= 1) throw new RangeError(`normalQuantile expects 0 < p < 1, got ${p}`);

  const a = [-39.6968302866538, 220.946098424521, -275.928510446969,
             138.357751867269, -30.6647980661472, 2.50662827745924];
  const b = [-54.4760987982241, 161.585836858041, -155.698979859887,
             66.8013118877197, -13.2806815528857];
  const c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184,
             -2.54973253934373, 4.37466414146497, 2.93816398269878];
  const d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742];

  const pLow = 0.02425;
  const pHigh = 1 - pLow;

  if (p < pLow) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (p > pHigh) {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  const q = p - 0.5;
  const r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
    (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

/** log Γ(x), Lanczos approximation. */
function logGamma(x: number): number {
  const cof = [76.18009172947146, -86.50532032941677, 24.01409824083091,
               -1.231739572450155, 0.1208650973866179e-2, -0.5395239384953e-5];
  let y = x;
  const tmp = x + 5.5 - (x + 0.5) * Math.log(x + 5.5);
  let ser = 1.000000000190015;
  for (let j = 0; j < 6; j += 1) {
    y += 1;
    ser += cof[j] / y;
  }
  return -tmp + Math.log((2.5066282746310005 * ser) / x);
}

/** Regularised upper incomplete gamma Q(a, x) = 1 - P(a, x). */
function gammaQ(a: number, x: number): number {
  if (x < 0 || a <= 0) throw new RangeError('gammaQ: invalid arguments');
  if (x === 0) return 1;

  if (x < a + 1) {
    // Series representation for P(a, x), then complement.
    let ap = a;
    let sum = 1 / a;
    let del = sum;
    for (let n = 0; n < 200; n += 1) {
      ap += 1;
      del *= x / ap;
      sum += del;
      if (Math.abs(del) < Math.abs(sum) * 1e-12) break;
    }
    return 1 - sum * Math.exp(-x + a * Math.log(x) - logGamma(a));
  }

  // Continued fraction (Lentz) for Q(a, x) directly — stable in the tail.
  const tiny = 1e-300;
  let b = x + 1 - a;
  let c = 1 / tiny;
  let d = 1 / b;
  let h = d;
  for (let i = 1; i <= 200; i += 1) {
    const an = -i * (i - a);
    b += 2;
    d = an * d + b;
    if (Math.abs(d) < tiny) d = tiny;
    c = b + an / c;
    if (Math.abs(c) < tiny) c = tiny;
    d = 1 / d;
    const del = d * c;
    h *= del;
    if (Math.abs(del - 1) < 1e-12) break;
  }
  return Math.exp(-x + a * Math.log(x) - logGamma(a)) * h;
}

/**
 * Exact two-sided binomial test: P(an outcome at least as extreme as `successes`
 * | true rate is `p`), summing every outcome whose probability is no greater
 * than the observed one.
 *
 * Exact rather than normal-approximated because the place this gets used —
 * paired comparisons like interleaving, where each query is one coin flip — is
 * often run on a few hundred queries, and that is where the approximation is
 * worst.
 */
export function binomialTest(successes: number, trials: number, p = 0.5): number {
  if (trials <= 0) return 1;
  if (successes < 0 || successes > trials) throw new RangeError('successes must be within trials');

  const logChoose = (n: number, k: number): number =>
    logGamma(n + 1) - logGamma(k + 1) - logGamma(n - k + 1);
  const logPmf = (k: number): number =>
    logChoose(trials, k) + k * Math.log(p) + (trials - k) * Math.log(1 - p);

  const observed = logPmf(successes);
  let total = 0;
  for (let k = 0; k <= trials; k += 1) {
    const lp = logPmf(k);
    // 1e-9 slack: outcomes with equal probability must both count.
    if (lp <= observed + 1e-9) total += Math.exp(lp);
  }
  return Math.min(1, total);
}

/** Upper-tail p-value of a chi-square statistic. */
export const chiSquarePValue = (chiSquare: number, degreesOfFreedom: number): number =>
  chiSquare <= 0 ? 1 : gammaQ(degreesOfFreedom / 2, chiSquare / 2);

/* ------------------------------------------------------------------ *
 * Planning: how big, how long
 * ------------------------------------------------------------------ */

export interface SampleSizeInput {
  /** Current conversion rate of the control arm, 0..1. */
  baselineRate: number;
  /**
   * Smallest effect worth detecting. `absolute` means percentage *points*
   * (0.02 = 10% → 12%); `relative` means a lift (0.05 = 10% → 10.5%).
   */
  minimumDetectableEffect: number;
  effectType?: 'absolute' | 'relative';
  /** False-positive rate. 0.05 by convention, not by law. */
  alpha?: number;
  /** 1 − β. 0.8 by convention; 0.9 when the decision is expensive to get wrong. */
  power?: number;
  /** One-sided tests need a pre-registered direction. Default two-sided. */
  twoSided?: boolean;
}

/**
 * Units per arm needed to detect `minimumDetectableEffect` with the given
 * α and power. Normal approximation to the binomial — fine above a few hundred
 * conversions per arm, which is anywhere you'd run a fixed-horizon test anyway.
 *
 * Read the output as a *budget*, not a target: if the answer is more traffic
 * than the surface gets in a quarter, the honest move is to pick a different
 * metric, a bigger change, or a different method — not to run it anyway and
 * squint at the result.
 */
export function sampleSizePerVariant({
  baselineRate,
  minimumDetectableEffect,
  effectType = 'absolute',
  alpha = 0.05,
  power = 0.8,
  twoSided = true,
}: SampleSizeInput): number {
  if (baselineRate <= 0 || baselineRate >= 1) {
    throw new RangeError('baselineRate must be strictly between 0 and 1');
  }
  const delta =
    effectType === 'relative' ? baselineRate * minimumDetectableEffect : minimumDetectableEffect;
  if (delta === 0) throw new RangeError('minimumDetectableEffect must be non-zero');

  const p1 = baselineRate;
  const p2 = Math.min(1 - 1e-9, Math.max(1e-9, baselineRate + delta));
  const pBar = (p1 + p2) / 2;

  const zAlpha = normalQuantile(1 - alpha / (twoSided ? 2 : 1));
  const zBeta = normalQuantile(power);

  const numerator =
    zAlpha * Math.sqrt(2 * pBar * (1 - pBar)) +
    zBeta * Math.sqrt(p1 * (1 - p1) + p2 * (1 - p2));

  return Math.ceil((numerator * numerator) / ((p2 - p1) * (p2 - p1)));
}

/** Inverse of the above: what can this much traffic actually see? */
export function detectableEffect({
  baselineRate,
  samplePerVariant,
  alpha = 0.05,
  power = 0.8,
  twoSided = true,
}: {
  baselineRate: number;
  samplePerVariant: number;
  alpha?: number;
  power?: number;
  twoSided?: boolean;
}): { absolute: number; relative: number } {
  const zAlpha = normalQuantile(1 - alpha / (twoSided ? 2 : 1));
  const zBeta = normalQuantile(power);
  // Symmetric-variance approximation: delta = (zα + zβ)·sqrt(2p(1−p)/n).
  const absolute =
    (zAlpha + zBeta) * Math.sqrt((2 * baselineRate * (1 - baselineRate)) / samplePerVariant);
  return { absolute, relative: absolute / baselineRate };
}

/** Days to reach the required sample, given daily eligible traffic. */
export const daysToRun = (
  samplePerVariant: number,
  dailyUnitsPerVariant: number,
): number => Math.ceil(samplePerVariant / Math.max(1, dailyUnitsPerVariant));

/* ------------------------------------------------------------------ *
 * Readout
 * ------------------------------------------------------------------ */

export interface ArmResult {
  /** Units *exposed* to this arm (not units assigned — see docs § Dilution). */
  units: number;
  conversions: number;
}

export interface TestResult {
  controlRate: number;
  treatmentRate: number;
  absoluteLift: number;
  relativeLift: number;
  zScore: number;
  pValue: number;
  /** Confidence interval on the absolute difference, at 1 − alpha. */
  confidenceInterval: [number, number];
  significant: boolean;
}

/**
 * Two-proportion z-test.
 *
 * Two different standard errors on purpose: the test statistic uses the
 * *pooled* rate (correct under H₀: both arms share one rate), the interval
 * uses the *unpooled* rate (the arms genuinely differ under H₁). Using one for
 * both is a common and mostly harmless inconsistency — until the result sits
 * on the boundary and the p-value and the CI disagree about significance.
 */
export function twoProportionZTest(
  control: ArmResult,
  treatment: ArmResult,
  alpha = 0.05,
): TestResult {
  if (control.units <= 0 || treatment.units <= 0) {
    throw new RangeError('both arms need at least one exposed unit');
  }
  const p1 = control.conversions / control.units;
  const p2 = treatment.conversions / treatment.units;

  const pooled = (control.conversions + treatment.conversions) / (control.units + treatment.units);
  const sePooled = Math.sqrt(pooled * (1 - pooled) * (1 / control.units + 1 / treatment.units));
  const zScore = sePooled === 0 ? 0 : (p2 - p1) / sePooled;
  const pValue = 2 * (1 - normalCdf(Math.abs(zScore)));

  const seUnpooled = Math.sqrt(
    (p1 * (1 - p1)) / control.units + (p2 * (1 - p2)) / treatment.units,
  );
  const zCritical = normalQuantile(1 - alpha / 2);
  const diff = p2 - p1;

  return {
    controlRate: p1,
    treatmentRate: p2,
    absoluteLift: diff,
    relativeLift: p1 === 0 ? Infinity : diff / p1,
    zScore,
    pValue,
    confidenceInterval: [diff - zCritical * seUnpooled, diff + zCritical * seUnpooled],
    significant: pValue < alpha,
  };
}

/* ------------------------------------------------------------------ *
 * Trust checks
 * ------------------------------------------------------------------ */

export interface SrmResult {
  chiSquare: number;
  pValue: number;
  /** True ⇒ the split is not what you configured. Do not read the metrics. */
  mismatch: boolean;
  observed: number[];
  expected: number[];
}

/**
 * Sample Ratio Mismatch: a chi-square goodness-of-fit test on the arm sizes.
 *
 * The highest-value five lines of statistics in the whole file. A 50/50 test
 * that lands 50.3/49.7 on a million users is not bad luck — it is a bug
 * (a redirect that drops slow clients, an exposure that fires later in one arm,
 * a bot filter that hits one arm harder), and it means the *metrics* are
 * biased too. The convention is a strict threshold (p < 0.001) and a hard stop:
 * on SRM you debug the pipeline, you do not interpret the result.
 */
export function checkSampleRatioMismatch(
  observed: ReadonlyArray<number>,
  expectedWeights: ReadonlyArray<number>,
  threshold = 0.001,
): SrmResult {
  if (observed.length !== expectedWeights.length || observed.length < 2) {
    throw new RangeError('observed and expectedWeights must align and have ≥ 2 arms');
  }
  const totalObserved = observed.reduce((a, b) => a + b, 0);
  const totalWeight = expectedWeights.reduce((a, b) => a + b, 0);
  const expected = expectedWeights.map((w) => (w / totalWeight) * totalObserved);

  const chiSquare = observed.reduce((acc, o, i) => {
    const e = expected[i];
    return e === 0 ? acc : acc + ((o - e) * (o - e)) / e;
  }, 0);

  const pValue = chiSquarePValue(chiSquare, observed.length - 1);
  return { chiSquare, pValue, mismatch: pValue < threshold, observed: [...observed], expected };
}

/**
 * Benjamini–Hochberg. Twenty metrics at α = 0.05 produce one "significant"
 * result by chance per readout; BH controls the *false discovery rate* across
 * a family instead, which is the right knob for a dashboard of guardrails.
 * Returns, for each input p-value, whether it survives at FDR = q.
 */
export function benjaminiHochberg(
  pValues: ReadonlyArray<number>,
  q = 0.05,
): boolean[] {
  const indexed = pValues.map((p, i) => ({ p, i })).sort((a, b) => a.p - b.p);
  const m = indexed.length;
  let cutoffRank = 0;
  indexed.forEach(({ p }, rank) => {
    if (p <= ((rank + 1) / m) * q) cutoffRank = rank + 1;
  });
  const result = new Array<boolean>(m).fill(false);
  for (let rank = 0; rank < cutoffRank; rank += 1) result[indexed[rank].i] = true;
  return result;
}
