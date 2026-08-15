/**
 * Core vocabulary for the experimentation framework.
 *
 * One rule drives every type here: an *assignment* (which variant a unit sees)
 * and an *exposure* (the fact that a unit actually saw it) are different
 * events. Analysis runs on exposures; rendering runs on assignments. Mixing
 * them is the single most common way client-side A/B tests lie — see
 * docs/ab-testing.md § "Dilution".
 */

export type VariantKey = string;

export interface Variant<P = unknown> {
  /** Stable id. `control` by convention for the baseline arm. */
  key: VariantKey;
  /** Relative weight. `[1, 1]` is a 50/50 split, `[9, 1]` a 90/10 ramp. */
  weight: number;
  /** Whatever the UI needs to render this arm (copy, delays, URLs, flags). */
  payload?: P;
}

export type ExperimentStatus = 'DRAFT' | 'RUNNING' | 'PAUSED' | 'COMPLETED';

/** Attributes the audience predicate can filter on (country, plan, app version…). */
export interface TargetingContext {
  readonly [attribute: string]: unknown;
}

export interface Experiment<P = unknown> {
  /** Stable, human-readable id — also the default randomisation salt. */
  key: string;
  status: ExperimentStatus;
  /** At least two arms. The first is treated as control unless `control` is set. */
  variants: ReadonlyArray<Variant<P>>;
  /** Share of *eligible* units allowed into the experiment, 0..1. */
  traffic: number;
  /** Explicit control key; defaults to `variants[0].key`. */
  control?: VariantKey;
  /**
   * Randomisation salt. Defaults to `key`. Change it to deliberately
   * re-randomise (a "re-shuffle"), e.g. when re-running a fixed experiment
   * and you don't want the same users in the same arms.
   */
  salt?: string;
  /** Eligibility gate. Units that fail it are never enrolled and never exposed. */
  audience?: (context: TargetingContext) => boolean;
}

export type AssignmentReason =
  /** Unit is in the experiment; `variant` came out of the hash. */
  | 'ENROLLED'
  /** Someone pinned the variant (QA query param, storage, staff override). */
  | 'FORCED'
  /** No experiment with that key is registered. */
  | 'UNKNOWN_EXPERIMENT'
  /** Experiment is DRAFT / PAUSED / COMPLETED. */
  | 'NOT_RUNNING'
  /** Failed the audience predicate. */
  | 'NOT_ELIGIBLE'
  /** Eligible, but outside the traffic ramp (the holdback). */
  | 'OUT_OF_TRAFFIC'
  /** Misconfigured experiment (no variants, all weights <= 0). */
  | 'INVALID';

export interface Assignment<P = unknown> {
  experimentKey: string;
  variant: VariantKey;
  payload?: P;
  /**
   * True only when the unit is genuinely randomised into the experiment.
   * Exposure is logged — and the unit counts in the analysis — only when this
   * is true. Forced assignments are deliberately NOT enrolled: QA must never
   * pollute the results.
   */
  enrolled: boolean;
  reason: AssignmentReason;
  /** The [0,1) draw used for the variant split. Handy for debugging/audits. */
  bucket: number;
}

export interface ExposureEvent {
  experimentKey: string;
  variant: VariantKey;
  unitId: string;
  /** ms since epoch — injected in tests, so assertions stay deterministic. */
  timestamp: number;
}

/** DI seam for the analytics pipe. Same idiom as `submitFeedback` in the widget. */
export type ExposureTracker = (event: ExposureEvent) => void;
