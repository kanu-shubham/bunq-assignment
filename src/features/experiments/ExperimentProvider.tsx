import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
} from 'react';
import { assign } from './core/assign';
import { Overrides } from './core/overrides';
import {
  Assignment,
  Experiment,
  ExposureEvent,
  ExposureTracker,
  TargetingContext,
} from './types';

export interface ExperimentProviderProps {
  /** Randomisation unit. Stable per user — see docs/ab-testing.md § "Unit". */
  unitId: string;
  experiments: ReadonlyArray<Experiment<unknown>>;
  attributes?: TargetingContext;
  overrides?: Overrides;
  /** DI seam for analytics. Omit in tests you don't care about, inject a mock when you do. */
  track?: ExposureTracker;
  /** Injected clock keeps exposure timestamps deterministic under test. */
  now?: () => number;
  children?: React.ReactNode;
}

interface ExperimentContextValue {
  unitId: string;
  registry: ReadonlyMap<string, Experiment<unknown>>;
  attributes: TargetingContext;
  overrides: Overrides;
  logExposure: (assignment: Assignment<unknown>) => void;
}

const MISSING = Symbol('no-experiment-provider');

// Frozen module-level defaults: `= {}` in the parameter list would mint a new
// object every render, invalidating every downstream useMemo for nothing.
const NO_ATTRIBUTES: TargetingContext = Object.freeze({});
const NO_OVERRIDES: Overrides = Object.freeze({});

const ExperimentContext = createContext<ExperimentContextValue | typeof MISSING>(MISSING);

const unknownAssignment = (key: string): Assignment<never> => ({
  experimentKey: key,
  variant: 'control',
  enrolled: false,
  reason: 'UNKNOWN_EXPERIMENT',
  bucket: 0,
});

export function ExperimentProvider({
  unitId,
  experiments,
  attributes = NO_ATTRIBUTES,
  overrides = NO_OVERRIDES,
  track,
  now = Date.now,
  children,
}: ExperimentProviderProps): JSX.Element {
  const registry = useMemo(
    () => new Map(experiments.map((experiment) => [experiment.key, experiment])),
    [experiments],
  );

  // Exposure is logged at most once per (unit, experiment, variant) per
  // provider lifetime. Warehouses dedupe anyway, but a component that
  // re-renders 60×/s would otherwise put 60 events/s on the wire for no extra
  // information. The unit id is part of the key rather than something we reset
  // on: child effects run before parent effects, so a parent effect that
  // cleared the set would wipe the exposure its own children had just logged.
  const seen = useRef(new Set<string>());
  const trackRef = useRef(track);
  trackRef.current = track;
  const nowRef = useRef(now);
  nowRef.current = now;

  const logExposure = useCallback((assignment: Assignment<unknown>) => {
    // Only genuinely randomised units count. FORCED / OUT_OF_TRAFFIC /
    // NOT_ELIGIBLE units render something, but they are not in the experiment
    // and must never appear in the analysis.
    if (!assignment.enrolled) return;
    const dedupeKey = `${unitId}:${assignment.experimentKey}:${assignment.variant}`;
    if (seen.current.has(dedupeKey)) return;
    seen.current.add(dedupeKey);

    const event: ExposureEvent = {
      experimentKey: assignment.experimentKey,
      variant: assignment.variant,
      unitId,
      timestamp: nowRef.current(),
    };
    trackRef.current?.(event);
  }, [unitId]);

  const value = useMemo<ExperimentContextValue>(
    () => ({ unitId, registry, attributes, overrides, logExposure }),
    [unitId, registry, attributes, overrides, logExposure],
  );

  return <ExperimentContext.Provider value={value}>{children}</ExperimentContext.Provider>;
}

function useExperimentContext(): ExperimentContextValue {
  const ctx = useContext(ExperimentContext);
  if (ctx === MISSING) {
    throw new Error(
      'useExperiment must be rendered inside <ExperimentProvider>. ' +
        'Wrap the app (or the test) in a provider.',
    );
  }
  return ctx;
}

export interface UseExperimentOptions {
  /**
   * Set to false while the component is mounted but the treatment is not on
   * screen yet (collapsed accordion, modal that has not opened). Logging an
   * exposure for a user who never saw the change dilutes the effect towards
   * zero — the most expensive mistake in client-side experimentation.
   */
  exposed?: boolean;
}

/**
 * Read the assignment for one experiment and log exposure exactly once,
 * *in an effect* — never during render. Render must stay pure: React can
 * render a component and throw the result away (StrictMode, Suspense retries,
 * concurrent re-entry), and every one of those would have fired a bogus
 * exposure.
 */
export function useExperiment<P = unknown>(
  experimentKey: string,
  { exposed = true }: UseExperimentOptions = {},
): Assignment<P> {
  const { unitId, registry, attributes, overrides, logExposure } = useExperimentContext();

  const assignment = useMemo<Assignment<P>>(() => {
    const experiment = registry.get(experimentKey) as Experiment<P> | undefined;
    if (!experiment) return unknownAssignment(experimentKey);
    return assign<P>({
      experiment,
      unitId,
      attributes,
      forcedVariant: overrides[experimentKey],
    });
  }, [experimentKey, registry, unitId, attributes, overrides]);

  useEffect(() => {
    if (exposed) logExposure(assignment as Assignment<unknown>);
  }, [exposed, assignment, logExposure]);

  return assignment;
}

/** Sugar for the common "which arm am I in" read. */
export const useVariant = (experimentKey: string, options?: UseExperimentOptions): string =>
  useExperiment(experimentKey, options).variant;

export interface ExperimentProps {
  name: string;
  exposed?: boolean;
  /** One child per variant key; `default` catches anything unmapped. */
  variants: Record<string, React.ReactNode>;
}

/** Declarative form, for when a component just swaps a subtree. */
export function ExperimentSwitch({ name, exposed, variants }: ExperimentProps): JSX.Element {
  const variant = useVariant(name, { exposed });
  return <>{variants[variant] ?? variants.default ?? null}</>;
}

export const __test__ = { ExperimentContext };
