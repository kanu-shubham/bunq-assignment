/**
 * Off-policy evaluation: what would the new model have scored, using logs from
 * the old one?
 *
 * This is the estimator an ML engineer reaches for *before* asking for traffic.
 * Every logged decision carries the probability with which the serving policy
 * took it (the **propensity**). If the logging policy was stochastic, you can
 * re-weight the logged rewards to estimate the value of a different policy,
 * offline, on data you already have.
 *
 *     V_IPS(π_e) = (1/n) Σ  (π_e(aᵢ|xᵢ) / π_b(aᵢ|xᵢ)) · rᵢ
 *
 * Unbiased, and frequently useless: the weights explode when the two policies
 * disagree, which is exactly when you wanted an answer. That's what the
 * effective sample size is for — it tells you when the estimate is really
 * resting on 40 of your 4 million logged events.
 *
 * The hard prerequisite is one you must build *into serving before you need
 * it*: the propensity has to be logged at decision time, and it has to be
 * non-zero for every action the new policy might take. A deterministic argmax
 * policy logs propensity 1.0 on one action and 0 everywhere else, which makes
 * every one of these estimators undefined. Buying that coverage means spending
 * a slice of traffic on exploration (ε-greedy, softmax sampling) — a design
 * decision, made months earlier, that decides whether offline evaluation is
 * available to you at all.
 */

export interface LoggedDecision {
  /** Probability the *logging* (production) policy took this action. Must be > 0. */
  loggingPropensity: number;
  /** Probability the *evaluated* (candidate) policy would take the same action. */
  targetPropensity: number;
  /** Observed reward: click, conversion, watch time, margin. */
  reward: number;
  /**
   * Optional reward model prediction q̂(x, a) for the logged action — enables
   * the doubly robust estimator.
   */
  rewardModelForLoggedAction?: number;
  /**
   * Optional E_{a~π_e}[q̂(x, a)]: the reward model's expectation under the
   * candidate policy. Required alongside the field above for doubly robust.
   */
  rewardModelUnderTargetPolicy?: number;
}

export interface OffPolicyEstimate {
  /** Estimated average reward under the candidate policy. */
  value: number;
  /** Standard error of that estimate (i.i.d. approximation). */
  standardError: number;
  confidenceInterval: [number, number];
  /**
   * Kish effective sample size, (Σw)² / Σw². If this is a small fraction of n,
   * a handful of logged events carry the whole estimate and the number is not
   * trustworthy however tight its interval looks.
   */
  effectiveSampleSize: number;
  /** Largest single importance weight — the other half of the same diagnostic. */
  maxWeight: number;
  /** Fraction of events whose weight hit the clip, if clipping was applied. */
  clippedFraction: number;
}

const Z_95 = 1.959963984540054;

function summarise(
  contributions: ReadonlyArray<number>,
  weights: ReadonlyArray<number>,
  clipped: number,
  scale = 1,
): OffPolicyEstimate {
  const n = contributions.length;
  const value = contributions.reduce((a, b) => a + b, 0) / n / scale;
  const variance =
    n < 2
      ? 0
      : contributions.reduce((acc, c) => {
          const d = c / scale - value;
          return acc + d * d;
        }, 0) /
        (n - 1) /
        n;
  const standardError = Math.sqrt(Math.max(0, variance));
  const sumW = weights.reduce((a, b) => a + b, 0);
  const sumW2 = weights.reduce((a, b) => a + b * b, 0);

  return {
    value,
    standardError,
    confidenceInterval: [value - Z_95 * standardError, value + Z_95 * standardError],
    effectiveSampleSize: sumW2 === 0 ? 0 : (sumW * sumW) / sumW2,
    maxWeight: weights.reduce((a, b) => Math.max(a, b), 0),
    clippedFraction: n === 0 ? 0 : clipped / n,
  };
}

function weightsOf(
  log: ReadonlyArray<LoggedDecision>,
  clip: number,
): { weights: number[]; clipped: number } {
  const weights: number[] = [];
  let clipped = 0;
  for (const d of log) {
    if (!(d.loggingPropensity > 0)) {
      throw new RangeError(
        'loggingPropensity must be > 0 for every logged decision — a deterministic ' +
          'policy leaves no support to evaluate against',
      );
    }
    const raw = d.targetPropensity / d.loggingPropensity;
    if (raw > clip) clipped += 1;
    weights.push(Math.min(raw, clip));
  }
  return { weights, clipped };
}

/**
 * Inverse propensity scoring. Unbiased when the logging policy has support
 * everywhere the candidate does — and only then.
 *
 * `clip` caps the importance weights. Clipping trades a known bias (the
 * estimate is pulled toward the logging policy's value) for a large variance
 * reduction. Report both the clipped and unclipped numbers; if they disagree
 * wildly, the honest answer is "the logs cannot answer this question".
 */
export function inversePropensityScoring(
  log: ReadonlyArray<LoggedDecision>,
  clip = Infinity,
): OffPolicyEstimate {
  if (log.length === 0) throw new RangeError('empty log');
  const { weights, clipped } = weightsOf(log, clip);
  const contributions = log.map((d, i) => weights[i] * d.reward);
  return summarise(contributions, weights, clipped);
}

/**
 * Self-normalised IPS. Divides by the mean weight instead of n, which caps the
 * damage a single huge weight can do. Slightly biased, far lower variance, and
 * the sane default in practice.
 */
export function selfNormalisedIps(
  log: ReadonlyArray<LoggedDecision>,
  clip = Infinity,
): OffPolicyEstimate {
  if (log.length === 0) throw new RangeError('empty log');
  const { weights, clipped } = weightsOf(log, clip);
  const meanWeight = weights.reduce((a, b) => a + b, 0) / weights.length;
  const contributions = log.map((d, i) => weights[i] * d.reward);
  return summarise(contributions, weights, clipped, meanWeight === 0 ? 1 : meanWeight);
}

/**
 * Doubly robust. Uses a reward model as a baseline and importance-weights only
 * its *residual*:
 *
 *     V_DR = (1/n) Σ [ q̂(xᵢ, π_e) + wᵢ·(rᵢ − q̂(xᵢ, aᵢ)) ]
 *
 * "Doubly" because it stays consistent if *either* the propensities or the
 * reward model are right. When the reward model is decent the residuals are
 * small, so the exploding weights multiply something near zero and the variance
 * collapses. This is the estimator to reach for when you have a reward model
 * lying around — and as an ML engineer, you always do.
 *
 * The reward model must be fit out-of-fold (cross-fitted) on the logged data;
 * fitting it on the same rows you then evaluate on smuggles the outcome into
 * the baseline and reintroduces the bias you were trying to remove.
 */
export function doublyRobust(
  log: ReadonlyArray<LoggedDecision>,
  clip = Infinity,
): OffPolicyEstimate {
  if (log.length === 0) throw new RangeError('empty log');
  for (const d of log) {
    if (
      d.rewardModelForLoggedAction === undefined ||
      d.rewardModelUnderTargetPolicy === undefined
    ) {
      throw new RangeError(
        'doublyRobust needs rewardModelForLoggedAction and rewardModelUnderTargetPolicy ' +
          'on every decision',
      );
    }
  }
  const { weights, clipped } = weightsOf(log, clip);
  const contributions = log.map(
    (d, i) =>
      (d.rewardModelUnderTargetPolicy as number) +
      weights[i] * (d.reward - (d.rewardModelForLoggedAction as number)),
  );
  return summarise(contributions, weights, clipped);
}

/**
 * The go/no-go check, run before you look at any estimate above.
 *
 * `ratio` is the effective sample size as a share of the log. Below ~10% the
 * estimate is resting on a small, self-selected slice of traffic; treat it as a
 * hint about direction, never as a decision.
 */
export function supportDiagnostics(
  log: ReadonlyArray<LoggedDecision>,
  minimumEffectiveRatio = 0.1,
): { effectiveSampleSize: number; ratio: number; sufficient: boolean; maxWeight: number } {
  const { weights } = weightsOf(log, Infinity);
  const sumW = weights.reduce((a, b) => a + b, 0);
  const sumW2 = weights.reduce((a, b) => a + b * b, 0);
  const ess = sumW2 === 0 ? 0 : (sumW * sumW) / sumW2;
  const ratio = ess / log.length;
  return {
    effectiveSampleSize: ess,
    ratio,
    sufficient: ratio >= minimumEffectiveRatio,
    maxWeight: weights.reduce((a, b) => Math.max(a, b), 0),
  };
}
