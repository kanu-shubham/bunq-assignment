import {
  benjaminiHochberg,
  checkSampleRatioMismatch,
  chiSquarePValue,
  daysToRun,
  detectableEffect,
  normalCdf,
  normalQuantile,
  sampleSizePerVariant,
  twoProportionZTest,
} from './stats';

describe('normal primitives', () => {
  test('Φ matches known values', () => {
    expect(normalCdf(0)).toBeCloseTo(0.5, 6);
    expect(normalCdf(1.959964)).toBeCloseTo(0.975, 5);
    expect(normalCdf(-1.959964)).toBeCloseTo(0.025, 5);
    expect(normalCdf(2.575829)).toBeCloseTo(0.995, 5);
  });

  test('Φ⁻¹ matches the z-values every analyst has memorised', () => {
    expect(normalQuantile(0.975)).toBeCloseTo(1.959964, 5);
    expect(normalQuantile(0.995)).toBeCloseTo(2.575829, 5);
    expect(normalQuantile(0.8)).toBeCloseTo(0.841621, 5);
    expect(normalQuantile(0.9)).toBeCloseTo(1.281552, 5);
  });

  test('Φ and Φ⁻¹ round-trip', () => {
    for (const p of [0.01, 0.1, 0.3, 0.5, 0.77, 0.99]) {
      expect(normalCdf(normalQuantile(p))).toBeCloseTo(p, 5);
    }
  });

  test('Φ⁻¹ rejects out-of-range input', () => {
    expect(() => normalQuantile(0)).toThrow(RangeError);
    expect(() => normalQuantile(1)).toThrow(RangeError);
  });

  test('chi-square tail matches the χ²(1) critical value', () => {
    expect(chiSquarePValue(3.8415, 1)).toBeCloseTo(0.05, 4);
    expect(chiSquarePValue(5.9915, 2)).toBeCloseTo(0.05, 4);
    expect(chiSquarePValue(0, 1)).toBe(1);
  });
});

describe('sampleSizePerVariant', () => {
  test('10% baseline, +2pp, α=.05, power=.8 → ≈3,841 per arm', () => {
    const n = sampleSizePerVariant({ baselineRate: 0.1, minimumDetectableEffect: 0.02 });
    expect(n).toBeGreaterThan(3800);
    expect(n).toBeLessThan(3890);
  });

  test('relative and absolute MDE agree when they describe the same effect', () => {
    const absolute = sampleSizePerVariant({ baselineRate: 0.1, minimumDetectableEffect: 0.02 });
    const relative = sampleSizePerVariant({
      baselineRate: 0.1,
      minimumDetectableEffect: 0.2,
      effectType: 'relative',
    });
    expect(relative).toBe(absolute);
  });

  test('halving the MDE roughly quadruples the sample', () => {
    const big = sampleSizePerVariant({ baselineRate: 0.1, minimumDetectableEffect: 0.02 });
    const small = sampleSizePerVariant({ baselineRate: 0.1, minimumDetectableEffect: 0.01 });
    expect(small / big).toBeGreaterThan(3.7);
    expect(small / big).toBeLessThan(4.3);
  });

  test('more power costs more traffic', () => {
    const at80 = sampleSizePerVariant({ baselineRate: 0.1, minimumDetectableEffect: 0.02 });
    const at90 = sampleSizePerVariant({
      baselineRate: 0.1,
      minimumDetectableEffect: 0.02,
      power: 0.9,
    });
    expect(at90).toBeGreaterThan(at80);
  });

  test('rejects impossible baselines and zero effects', () => {
    expect(() => sampleSizePerVariant({ baselineRate: 0, minimumDetectableEffect: 0.01 })).toThrow();
    expect(() => sampleSizePerVariant({ baselineRate: 0.1, minimumDetectableEffect: 0 })).toThrow();
  });

  test('detectableEffect is the inverse of sampleSizePerVariant', () => {
    const n = sampleSizePerVariant({ baselineRate: 0.1, minimumDetectableEffect: 0.02 });
    const { absolute } = detectableEffect({ baselineRate: 0.1, samplePerVariant: n });
    expect(absolute).toBeCloseTo(0.02, 2);
  });

  test('daysToRun turns a sample budget into a calendar', () => {
    expect(daysToRun(3841, 1000)).toBe(4);
    expect(daysToRun(3841, 0)).toBe(3841);
  });
});

describe('twoProportionZTest', () => {
  test('detects a real 10% → 12% lift', () => {
    const result = twoProportionZTest(
      { units: 10_000, conversions: 1_000 },
      { units: 10_000, conversions: 1_200 },
    );
    expect(result.controlRate).toBeCloseTo(0.1, 6);
    expect(result.treatmentRate).toBeCloseTo(0.12, 6);
    expect(result.absoluteLift).toBeCloseTo(0.02, 6);
    expect(result.relativeLift).toBeCloseTo(0.2, 6);
    expect(result.zScore).toBeCloseTo(4.52, 1);
    expect(result.pValue).toBeLessThan(1e-4);
    expect(result.significant).toBe(true);
  });

  test('identical arms give z = 0, p = 1 and a CI straddling zero', () => {
    const result = twoProportionZTest(
      { units: 5_000, conversions: 500 },
      { units: 5_000, conversions: 500 },
    );
    expect(result.zScore).toBe(0);
    expect(result.pValue).toBeCloseTo(1, 5);
    expect(result.significant).toBe(false);
    expect(result.confidenceInterval[0]).toBeLessThan(0);
    expect(result.confidenceInterval[1]).toBeGreaterThan(0);
  });

  test('the same lift on small samples is not significant — the classic false win', () => {
    const result = twoProportionZTest(
      { units: 100, conversions: 10 },
      { units: 100, conversions: 12 },
    );
    expect(result.relativeLift).toBeCloseTo(0.2, 6);
    expect(result.pValue).toBeGreaterThan(0.05);
    expect(result.significant).toBe(false);
  });

  test('the CI excludes zero exactly when the result is significant', () => {
    const result = twoProportionZTest(
      { units: 20_000, conversions: 2_000 },
      { units: 20_000, conversions: 2_150 },
    );
    const excludesZero =
      result.confidenceInterval[0] > 0 || result.confidenceInterval[1] < 0;
    expect(excludesZero).toBe(result.significant);
  });

  test('rejects empty arms', () => {
    expect(() =>
      twoProportionZTest({ units: 0, conversions: 0 }, { units: 10, conversions: 1 }),
    ).toThrow(RangeError);
  });
});

describe('checkSampleRatioMismatch', () => {
  test('a clean 50/50 passes', () => {
    const srm = checkSampleRatioMismatch([10_000, 10_050], [1, 1]);
    expect(srm.mismatch).toBe(false);
    expect(srm.pValue).toBeGreaterThan(0.001);
  });

  test('a 0.3pp skew on a million users is a bug, not luck', () => {
    // χ² = 36 on 1 df — a 1-in-500-million coincidence, or a broken pipeline.
    const srm = checkSampleRatioMismatch([503_000, 497_000], [1, 1]);
    expect(srm.mismatch).toBe(true);
    expect(srm.pValue).toBeLessThan(1e-8);
  });

  test('handles uneven planned splits', () => {
    const srm = checkSampleRatioMismatch([90_000, 10_000], [9, 1]);
    expect(srm.mismatch).toBe(false);
    expect(srm.expected).toEqual([90_000, 10_000]);
  });

  test('catches a dropped arm in a three-way test', () => {
    const srm = checkSampleRatioMismatch([10_000, 10_000, 8_500], [1, 1, 1]);
    expect(srm.mismatch).toBe(true);
  });

  test('rejects mismatched inputs', () => {
    expect(() => checkSampleRatioMismatch([1, 2], [1])).toThrow(RangeError);
  });
});

describe('benjaminiHochberg', () => {
  test('keeps strong signals and drops the noise floor', () => {
    // Thresholds are (rank/m)·q = .01 .02 .03 .04 .05 — only the first two clear.
    const pValues = [0.001, 0.008, 0.039, 0.041, 0.9];
    expect(benjaminiHochberg(pValues, 0.05)).toEqual([true, true, false, false, false]);
  });

  test('is stricter than an uncorrected α on a wide dashboard', () => {
    const pValues = [0.04, ...Array(19).fill(0.6)];
    expect(benjaminiHochberg(pValues, 0.05)[0]).toBe(false);
  });
});
