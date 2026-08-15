export {
  ExperimentProvider,
  ExperimentSwitch,
  useExperiment,
  useVariant,
} from './ExperimentProvider';
export type {
  ExperimentProviderProps,
  ExperimentProps,
  UseExperimentOptions,
} from './ExperimentProvider';

export { assign } from './core/assign';
export type { AssignInput } from './core/assign';
export { fnv1a32, hash32, hashFraction, trafficBucket, variantBucket } from './core/hash';
export {
  parseOverridesFromSearch,
  resolveOverrides,
  STORAGE_KEY as OVERRIDES_STORAGE_KEY,
} from './core/overrides';
export type { Overrides, OverrideStorage } from './core/overrides';

export type {
  Assignment,
  AssignmentReason,
  Experiment,
  ExperimentStatus,
  ExposureEvent,
  ExposureTracker,
  TargetingContext,
  Variant,
  VariantKey,
} from './types';

export {
  benjaminiHochberg,
  checkSampleRatioMismatch,
  chiSquarePValue,
  daysToRun,
  detectableEffect,
  normalCdf,
  normalQuantile,
  sampleSizePerVariant,
  twoProportionZTest,
} from './analysis/stats';
export type { ArmResult, SampleSizeInput, SrmResult, TestResult } from './analysis/stats';

export { covariance, cupedCompare, cupedTheta, moments } from './analysis/cuped';
export type { CupedArm, CupedResult } from './analysis/cuped';
