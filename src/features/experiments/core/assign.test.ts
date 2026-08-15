import { assign } from './assign';
import { Experiment } from '../types';

const ids = (n: number): string[] => Array.from({ length: n }, (_, i) => `user-${i}`);

const experiment = (over: Partial<Experiment<string>> = {}): Experiment<string> => ({
  key: 'rating-copy',
  status: 'RUNNING',
  traffic: 1,
  variants: [
    { key: 'control', weight: 1, payload: 'How would you rate this feature?' },
    { key: 'treatment', weight: 1, payload: 'Enjoying this feature?' },
  ],
  ...over,
});

describe('assign', () => {
  test('is deterministic per unit', () => {
    const exp = experiment();
    const first = assign({ experiment: exp, unitId: 'user-42' });
    const second = assign({ experiment: exp, unitId: 'user-42' });
    expect(second).toEqual(first);
  });

  test('returns the variant payload alongside the key', () => {
    const exp = experiment();
    const a = assign({ experiment: exp, unitId: 'user-1' });
    expect(a.payload).toBe(exp.variants.find((v) => v.key === a.variant)?.payload);
  });

  test('splits 50/50 within tolerance', () => {
    const exp = experiment();
    const n = 10_000;
    const treatment = ids(n).filter(
      (id) => assign({ experiment: exp, unitId: id }).variant === 'treatment',
    ).length;
    expect(treatment / n).toBeGreaterThan(0.48);
    expect(treatment / n).toBeLessThan(0.52);
  });

  test('honours weights', () => {
    const exp = experiment({
      variants: [
        { key: 'control', weight: 9 },
        { key: 'treatment', weight: 1 },
      ],
    });
    const n = 20_000;
    const treatment = ids(n).filter(
      (id) => assign({ experiment: exp, unitId: id }).variant === 'treatment',
    ).length;
    expect(treatment / n).toBeGreaterThan(0.09);
    expect(treatment / n).toBeLessThan(0.11);
  });

  describe('traffic ramp', () => {
    test('lets roughly `traffic` of units in', () => {
      const exp = experiment({ traffic: 0.2 });
      const n = 20_000;
      const enrolled = ids(n).filter((id) => assign({ experiment: exp, unitId: id }).enrolled).length;
      expect(enrolled / n).toBeGreaterThan(0.18);
      expect(enrolled / n).toBeLessThan(0.22);
    });

    test('held-back units get control, not enrolled', () => {
      const exp = experiment({ traffic: 0 });
      const a = assign({ experiment: exp, unitId: 'user-7' });
      expect(a).toMatchObject({ variant: 'control', enrolled: false, reason: 'OUT_OF_TRAFFIC' });
    });

    test('ramping traffic never moves an already-enrolled unit', () => {
      // The property that makes a ramp safe: 1% → 5% → 25% → 100% only ever
      // *adds* units; nobody flips arms mid-experiment.
      const stages = [0.01, 0.05, 0.25, 0.5, 1];
      const locked = new Map<string, string>();

      for (const traffic of stages) {
        const exp = experiment({ traffic });
        for (const id of ids(3000)) {
          const a = assign({ experiment: exp, unitId: id });
          if (!a.enrolled) continue;
          const previous = locked.get(id);
          if (previous) expect(a.variant).toBe(previous);
          else locked.set(id, a.variant);
        }
      }
      expect(locked.size).toBeGreaterThan(0);
    });
  });

  describe('gates', () => {
    test.each(['DRAFT', 'PAUSED', 'COMPLETED'] as const)('%s → control, NOT_RUNNING', (status) => {
      const a = assign({ experiment: experiment({ status }), unitId: 'user-1' });
      expect(a).toMatchObject({ variant: 'control', enrolled: false, reason: 'NOT_RUNNING' });
    });

    test('audience predicate excludes ineligible units', () => {
      const exp = experiment({ audience: (ctx) => ctx.country === 'NL' });
      expect(assign({ experiment: exp, unitId: 'u', attributes: { country: 'NL' } })).toMatchObject({
        enrolled: true,
      });
      expect(assign({ experiment: exp, unitId: 'u', attributes: { country: 'DE' } })).toMatchObject({
        enrolled: false,
        reason: 'NOT_ELIGIBLE',
      });
    });

    test('empty unit id is a configuration error, not a coin flip', () => {
      expect(assign({ experiment: experiment(), unitId: '' })).toMatchObject({
        enrolled: false,
        reason: 'INVALID',
      });
    });

    test('single-arm experiment is INVALID', () => {
      const exp = experiment({ variants: [{ key: 'control', weight: 1 }] });
      expect(assign({ experiment: exp, unitId: 'u' }).reason).toBe('INVALID');
    });

    test('all-zero weights fall back to control', () => {
      const exp = experiment({
        variants: [
          { key: 'control', weight: 0 },
          { key: 'treatment', weight: 0 },
        ],
      });
      expect(assign({ experiment: exp, unitId: 'u' })).toMatchObject({
        variant: 'control',
        enrolled: false,
        reason: 'INVALID',
      });
    });

    test('explicit `control` key wins over declaration order', () => {
      const exp = experiment({ status: 'PAUSED', control: 'treatment' });
      expect(assign({ experiment: exp, unitId: 'u' }).variant).toBe('treatment');
    });
  });

  describe('forced variants', () => {
    test('pin the arm but never enrol the unit', () => {
      const a = assign({ experiment: experiment(), unitId: 'user-1', forcedVariant: 'treatment' });
      expect(a).toMatchObject({ variant: 'treatment', enrolled: false, reason: 'FORCED' });
    });

    test('work even on a paused experiment', () => {
      const a = assign({
        experiment: experiment({ status: 'PAUSED' }),
        unitId: 'user-1',
        forcedVariant: 'treatment',
      });
      expect(a.variant).toBe('treatment');
    });

    test('an unknown key is ignored rather than thrown', () => {
      const a = assign({ experiment: experiment(), unitId: 'user-1', forcedVariant: 'typo' });
      expect(a.reason).toBe('ENROLLED');
      expect(['control', 'treatment']).toContain(a.variant);
    });
  });

  test('changing the salt re-randomises the population', () => {
    const base = experiment();
    const reshuffled = experiment({ salt: 'rating-copy-v2' });
    const moved = ids(2000).filter(
      (id) =>
        assign({ experiment: base, unitId: id }).variant !==
        assign({ experiment: reshuffled, unitId: id }).variant,
    ).length;
    expect(moved / 2000).toBeGreaterThan(0.4);
  });
});
