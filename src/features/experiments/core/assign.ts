import {
  Assignment,
  Experiment,
  TargetingContext,
  VariantKey,
  Variant,
} from '../types';
import { trafficBucket, variantBucket } from './hash';

export interface AssignInput<P = unknown> {
  experiment: Experiment<P>;
  /** The randomisation unit: user id, account id, or a persisted anonymous id. */
  unitId: string;
  attributes?: TargetingContext;
  /** QA / staff pin. Forced units are returned with `enrolled: false`. */
  forcedVariant?: VariantKey;
}

const controlOf = <P>(experiment: Experiment<P>): Variant<P> | undefined =>
  experiment.variants.find((v) => v.key === experiment.control) ?? experiment.variants[0];

const fallback = <P>(
  experiment: Experiment<P>,
  reason: Assignment<P>['reason'],
  bucket: number,
): Assignment<P> => {
  const control = controlOf(experiment);
  return {
    experimentKey: experiment.key,
    variant: control?.key ?? 'control',
    payload: control?.payload,
    enrolled: false,
    reason,
    bucket,
  };
};

/**
 * Pick a variant from the cumulative weight ranges.
 *
 * Order matters and must be stable: the arms are walked in declaration order,
 * so appending a new arm to the end of the list leaves the earlier arms'
 * ranges untouched... *only* if the weights of the existing arms are unchanged
 * AND the total is unchanged. In practice, adding an arm mid-flight re-weights
 * everyone — treat it as a new experiment with a new salt instead.
 */
function pickByWeight<P>(variants: ReadonlyArray<Variant<P>>, bucket: number): Variant<P> | null {
  const total = variants.reduce((sum, v) => sum + Math.max(0, v.weight), 0);
  if (total <= 0) return null;

  let cursor = 0;
  for (const variant of variants) {
    cursor += Math.max(0, variant.weight) / total;
    if (bucket < cursor) return variant;
  }
  // Only reachable through floating-point drift at bucket ≈ 1.
  return variants[variants.length - 1];
}

/**
 * Pure assignment. No I/O, no React, no globals — which is why it can be
 * unit-tested exhaustively and, in a real platform, shared verbatim with the
 * server-side SDK so both tiers agree on what a user sees.
 *
 * Evaluation order is deliberate: cheap/invariant gates first, hashing last.
 * `forcedVariant` beats everything so QA can pin an arm even on a PAUSED
 * experiment — but it never sets `enrolled`, so it never enters the analysis.
 */
export function assign<P = unknown>({
  experiment,
  unitId,
  attributes = {},
  forcedVariant,
}: AssignInput<P>): Assignment<P> {
  const salt = experiment.salt ?? experiment.key;

  if (experiment.variants.length < 2) return fallback(experiment, 'INVALID', 0);

  if (forcedVariant !== undefined) {
    const pinned = experiment.variants.find((v) => v.key === forcedVariant);
    if (pinned) {
      return {
        experimentKey: experiment.key,
        variant: pinned.key,
        payload: pinned.payload,
        enrolled: false,
        reason: 'FORCED',
        bucket: variantBucket(salt, unitId),
      };
    }
    // Unknown key — fall through rather than crash the UI on a typo'd URL.
  }

  if (experiment.status !== 'RUNNING') return fallback(experiment, 'NOT_RUNNING', 0);

  if (!unitId) return fallback(experiment, 'INVALID', 0);

  if (experiment.audience && !experiment.audience(attributes)) {
    return fallback(experiment, 'NOT_ELIGIBLE', 0);
  }

  const traffic = Math.min(1, Math.max(0, experiment.traffic));
  if (trafficBucket(salt, unitId) >= traffic) {
    return fallback(experiment, 'OUT_OF_TRAFFIC', 0);
  }

  const bucket = variantBucket(salt, unitId);
  const chosen = pickByWeight(experiment.variants, bucket);
  if (!chosen) return fallback(experiment, 'INVALID', bucket);

  return {
    experimentKey: experiment.key,
    variant: chosen.key,
    payload: chosen.payload,
    enrolled: true,
    reason: 'ENROLLED',
    bucket,
  };
}

export const __test__ = { pickByWeight, controlOf };
