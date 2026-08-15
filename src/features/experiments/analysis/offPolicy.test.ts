import {
  LoggedDecision,
  doublyRobust,
  inversePropensityScoring,
  selfNormalisedIps,
  supportDiagnostics,
} from './offPolicy';

function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/**
 * Two-action bandit. The logging policy is ε-greedy (it explores, which is what
 * makes the logs evaluable at all); the candidate policy prefers action 1 with
 * probability `targetPreference`. True reward rates: action 0 → 0.10,
 * action 1 → 0.20.
 */
function simulateLog(
  n: number,
  loggingPreference: number,
  targetPreference: number,
  seed: number,
  withRewardModel = false,
): LoggedDecision[] {
  const next = rng(seed);
  const trueReward = [0.1, 0.2];
  const out: LoggedDecision[] = [];
  for (let i = 0; i < n; i += 1) {
    const action = next() < loggingPreference ? 1 : 0;
    const loggingPropensity = action === 1 ? loggingPreference : 1 - loggingPreference;
    const targetPropensity = action === 1 ? targetPreference : 1 - targetPreference;
    const reward = next() < trueReward[action] ? 1 : 0;
    const decision: LoggedDecision = { loggingPropensity, targetPropensity, reward };
    if (withRewardModel) {
      // A decent (not perfect) reward model.
      const q = [0.11, 0.19];
      decision.rewardModelForLoggedAction = q[action];
      decision.rewardModelUnderTargetPolicy = targetPreference * q[1] + (1 - targetPreference) * q[0];
    }
    out.push(decision);
  }
  return out;
}

describe('inversePropensityScoring', () => {
  test('recovers the value of a policy the logs actually support', () => {
    // Candidate always picks action 1 → true value 0.20.
    const log = simulateLog(60_000, 0.5, 1, 42);
    const estimate = inversePropensityScoring(log);
    expect(estimate.value).toBeGreaterThan(0.18);
    expect(estimate.value).toBeLessThan(0.22);
    expect(estimate.confidenceInterval[0]).toBeLessThan(0.2);
    expect(estimate.confidenceInterval[1]).toBeGreaterThan(0.2);
  });

  test('is exact when the two policies are identical', () => {
    const log = simulateLog(20_000, 0.5, 0.5, 7);
    const estimate = inversePropensityScoring(log);
    const empirical = log.reduce((a, d) => a + d.reward, 0) / log.length;
    expect(estimate.value).toBeCloseTo(empirical, 9);
    expect(estimate.effectiveSampleSize).toBeCloseTo(log.length, 6);
  });

  test('refuses a deterministic logging policy', () => {
    expect(() =>
      inversePropensityScoring([{ loggingPropensity: 0, targetPropensity: 1, reward: 1 }]),
    ).toThrow(RangeError);
  });

  test('variance explodes when the policies barely overlap', () => {
    // Logging almost never takes the action the candidate always takes.
    const overlapping = inversePropensityScoring(simulateLog(20_000, 0.5, 1, 5));
    const disjoint = inversePropensityScoring(simulateLog(20_000, 0.02, 1, 5));
    expect(disjoint.standardError).toBeGreaterThan(overlapping.standardError * 3);
    expect(disjoint.maxWeight).toBeGreaterThan(overlapping.maxWeight * 5);
  });
});

describe('clipping', () => {
  test('trades bias for variance, and reports how much was clipped', () => {
    const log = simulateLog(20_000, 0.05, 1, 11);
    const raw = inversePropensityScoring(log);
    const clipped = inversePropensityScoring(log, 5);
    expect(clipped.standardError).toBeLessThan(raw.standardError);
    expect(clipped.clippedFraction).toBeGreaterThan(0);
    // Clipping pulls the estimate toward the logging policy's value.
    expect(clipped.value).toBeLessThan(raw.value);
  });
});

describe('selfNormalisedIps', () => {
  test('lands near the true value with less variance than plain IPS', () => {
    const log = simulateLog(40_000, 0.3, 1, 3);
    const ips = inversePropensityScoring(log);
    const snips = selfNormalisedIps(log);
    expect(snips.value).toBeGreaterThan(0.17);
    expect(snips.value).toBeLessThan(0.23);
    expect(snips.standardError).toBeLessThan(ips.standardError);
  });
});

describe('doublyRobust', () => {
  test('beats IPS on variance when a reward model is available', () => {
    const log = simulateLog(40_000, 0.3, 1, 17, true);
    const ips = inversePropensityScoring(log);
    const dr = doublyRobust(log);
    expect(dr.value).toBeGreaterThan(0.17);
    expect(dr.value).toBeLessThan(0.23);
    expect(dr.standardError).toBeLessThan(ips.standardError);
  });

  test('requires the reward model columns', () => {
    expect(() => doublyRobust(simulateLog(10, 0.5, 1, 1))).toThrow(RangeError);
  });
});

describe('supportDiagnostics', () => {
  test('passes when the policies overlap', () => {
    const diag = supportDiagnostics(simulateLog(10_000, 0.5, 0.6, 9));
    expect(diag.sufficient).toBe(true);
    expect(diag.ratio).toBeGreaterThan(0.9);
  });

  test('fails loudly when the estimate rests on a sliver of traffic', () => {
    const diag = supportDiagnostics(simulateLog(10_000, 0.01, 1, 9));
    expect(diag.sufficient).toBe(false);
    expect(diag.ratio).toBeLessThan(0.05);
  });
});
