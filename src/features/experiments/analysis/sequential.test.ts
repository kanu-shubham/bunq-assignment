import { alwaysValidDifference, alwaysValidRateDifference } from './sequential';
import { twoProportionZTest } from './stats';

function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/**
 * Monitor a stream of two arms, checking after every `step` units, and report
 * whether each decision rule ever fired. This is the peeking experiment from
 * docs/ab-testing.md, run against both rules.
 */
function monitorUnderNull(
  trials: number,
  looks: number,
  step: number,
  rate: number,
  seed: number,
): { fixedHorizonFpr: number; alwaysValidFpr: number } {
  const next = rng(seed);
  let fixedFired = 0;
  let sequentialFired = 0;

  for (let t = 0; t < trials; t += 1) {
    let nc = 0;
    let nt = 0;
    let cc = 0;
    let ct = 0;
    let fixed = false;
    let sequential = false;

    for (let look = 0; look < looks; look += 1) {
      for (let i = 0; i < step; i += 1) {
        if (next() < rate) cc += 1;
        if (next() < rate) ct += 1;
      }
      nc += step;
      nt += step;

      if (!fixed && twoProportionZTest({ units: nc, conversions: cc }, { units: nt, conversions: ct }).significant) {
        fixed = true;
      }
      if (
        !sequential &&
        alwaysValidRateDifference(
          { units: nc, conversions: cc },
          { units: nt, conversions: ct },
          { tuningSample: looks * step },
        ).decisive
      ) {
        sequential = true;
      }
    }
    if (fixed) fixedFired += 1;
    if (sequential) sequentialFired += 1;
  }
  return { fixedHorizonFpr: fixedFired / trials, alwaysValidFpr: sequentialFired / trials };
}

describe('alwaysValidDifference', () => {
  test('is wider than the fixed-horizon interval — that is the price of peeking', () => {
    const result = alwaysValidRateDifference(
      { units: 10_000, conversions: 1_000 },
      { units: 10_000, conversions: 1_020 },
      { tuningSample: 10_000 },
    );
    expect(result.inflationVsFixedHorizon).toBeGreaterThan(1.2);
    expect(result.inflationVsFixedHorizon).toBeLessThan(2);
  });

  test('narrows as data accumulates', () => {
    const early = alwaysValidRateDifference(
      { units: 1_000, conversions: 100 },
      { units: 1_000, conversions: 105 },
      { tuningSample: 50_000 },
    );
    const late = alwaysValidRateDifference(
      { units: 50_000, conversions: 5_000 },
      { units: 50_000, conversions: 5_250 },
      { tuningSample: 50_000 },
    );
    expect(late.halfWidth).toBeLessThan(early.halfWidth);
  });

  test('does not fire on a small early difference', () => {
    const result = alwaysValidRateDifference(
      { units: 200, conversions: 20 },
      { units: 200, conversions: 26 },
      { tuningSample: 20_000 },
    );
    expect(result.decisive).toBe(false);
    expect(result.direction).toBeNull();
  });

  test('fires on a large, real effect', () => {
    const result = alwaysValidRateDifference(
      { units: 20_000, conversions: 2_000 },
      { units: 20_000, conversions: 2_400 },
      { tuningSample: 20_000 },
    );
    expect(result.decisive).toBe(true);
    expect(result.direction).toBe('treatment');
    expect(result.confidenceSequence[0]).toBeGreaterThan(0);
  });

  test('detects a regression and names the winner', () => {
    const result = alwaysValidRateDifference(
      { units: 20_000, conversions: 2_400 },
      { units: 20_000, conversions: 2_000 },
      { tuningSample: 20_000 },
    );
    expect(result.direction).toBe('control');
  });

  test('accepts continuous metrics too', () => {
    const result = alwaysValidDifference({
      control: { mean: 4.0, variance: 9, units: 8_000 },
      treatment: { mean: 4.3, variance: 9, units: 8_000 },
      tuningSample: 8_000,
    });
    expect(result.difference).toBeCloseTo(0.3, 9);
    expect(result.decisive).toBe(true);
  });

  test('rejects empty arms', () => {
    expect(() =>
      alwaysValidDifference({
        control: { mean: 0, variance: 1, units: 0 },
        treatment: { mean: 0, variance: 1, units: 10 },
      }),
    ).toThrow(RangeError);
  });

  test('survives continuous monitoring where the fixed-horizon test does not', () => {
    // Both arms have the SAME true rate: every "significant" result is a false
    // positive. 10 looks at a 5% test should fire ~20% of the time; the
    // confidence sequence is built to stay under 5% however often you look.
    const { fixedHorizonFpr, alwaysValidFpr } = monitorUnderNull(300, 10, 400, 0.1, 20250815);

    expect(fixedHorizonFpr).toBeGreaterThan(0.1);
    expect(alwaysValidFpr).toBeLessThan(0.05);
    expect(alwaysValidFpr).toBeLessThan(fixedHorizonFpr);
  });
});
