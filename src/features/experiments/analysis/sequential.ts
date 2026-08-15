import { normalQuantile } from './stats';

/**
 * Always-valid inference: a confidence interval you are allowed to look at
 * whenever you like.
 *
 * Fixed-horizon statistics are valid at exactly one moment — the pre-registered
 * end of the test. That contract does not survive contact with an ML team: the
 * model is on a dashboard, someone is watching it, and the first "should we
 * roll back?" arrives on day one. Under continuous monitoring, a 5% test fires
 * false positives about 20% of the time across ten looks.
 *
 * A confidence *sequence* is a family of intervals, one per sample size, with
 * the guarantee holding **simultaneously for all n**:
 *
 *     P( ∃n : μ ∉ CIₙ ) ≤ α
 *
 * So "stop as soon as the interval excludes zero" is a legitimate rule. The
 * price is width: this normal-mixture boundary (Howard et al., 2021) is ~1.55×
 * the fixed-horizon interval at its tuning point, which means ~2.4× the sample
 * for the same power — and much wider than that early on, which is the point:
 * the early looks are exactly where a fixed-horizon test lies to you. That is
 * the honest cost of being allowed to peek, and it is usually worth paying for
 * a model rollout where the alternative is an unmonitored regression.
 *
 * `tuningSample` sets where the sequence is tightest — set it to the sample
 * size you'd have planned for a fixed-horizon test. Choosing it *after* looking
 * at the data forfeits the guarantee.
 */

/** x = nρ at the tuning point; ≈10 minimises the normal-mixture boundary width. */
const SEQUENCE_TUNING_CONSTANT = 10;

export interface SequentialArm {
  mean: number;
  /** Per-unit variance (p(1−p) for a rate metric). */
  variance: number;
  units: number;
}

export interface SequentialResult {
  difference: number;
  halfWidth: number;
  confidenceSequence: [number, number];
  /** True when the interval excludes zero — safe to act on at any look. */
  decisive: boolean;
  /** Which arm is ahead, once decisive. */
  direction: 'treatment' | 'control' | null;
  /** How much wider than the (invalid-under-peeking) fixed-horizon interval. */
  inflationVsFixedHorizon: number;
}

export function alwaysValidDifference({
  control,
  treatment,
  alpha = 0.05,
  tuningSample,
}: {
  control: SequentialArm;
  treatment: SequentialArm;
  alpha?: number;
  /** Sample per arm where the sequence is tightest. Defaults to the current n. */
  tuningSample?: number;
}): SequentialResult {
  if (control.units <= 0 || treatment.units <= 0) {
    throw new RangeError('both arms need at least one unit');
  }
  const standardError = Math.sqrt(
    control.variance / control.units + treatment.variance / treatment.units,
  );
  const n = Math.min(control.units, treatment.units);
  const target = Math.max(1, tuningSample ?? n);
  // ρ sets where the sequence is tightest. The boundary width depends on n only
  // through x = nρ, and minimising it over x puts the optimum near x ≈ 10 — so
  // ρ = 10/target makes the sequence tightest at the sample you planned for.
  // (ρ = 1/target, the obvious choice, is 20% wider there.)
  const rho = SEQUENCE_TUNING_CONSTANT / target;

  // Normal-mixture boundary, expressed as a multiple of the standard error.
  // half = se · sqrt( 2(nρ+1)/(nρ) · ln( √(nρ+1) / α ) )
  const nRho = n * rho;
  const multiplier = Math.sqrt(
    ((2 * (nRho + 1)) / nRho) * Math.log(Math.sqrt(nRho + 1) / alpha),
  );
  const halfWidth = standardError * multiplier;
  const difference = treatment.mean - control.mean;
  const lower = difference - halfWidth;
  const upper = difference + halfWidth;
  const decisive = lower > 0 || upper < 0;

  return {
    difference,
    halfWidth,
    confidenceSequence: [lower, upper],
    decisive,
    direction: decisive ? (difference > 0 ? 'treatment' : 'control') : null,
    inflationVsFixedHorizon: multiplier / normalQuantile(1 - alpha / 2),
  };
}

/** Convenience wrapper for the binary-metric case (clicks, conversions, catches). */
export const alwaysValidRateDifference = (
  control: { units: number; conversions: number },
  treatment: { units: number; conversions: number },
  options: { alpha?: number; tuningSample?: number } = {},
): SequentialResult => {
  const rate = (a: { units: number; conversions: number }): SequentialArm => {
    const p = a.conversions / a.units;
    return { mean: p, variance: p * (1 - p), units: a.units };
  };
  return alwaysValidDifference({ control: rate(control), treatment: rate(treatment), ...options });
};
