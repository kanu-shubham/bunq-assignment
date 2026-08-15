import {
  QueryOutcome,
  interleavingPreference,
  scoreQuery,
  teamDraftInterleave,
} from './interleaving';
import { binomialTest } from './stats';

const alwaysA = () => true;
const alwaysB = () => false;

function alternating(start = true): () => boolean {
  let flag = start;
  return () => {
    flag = !flag;
    return !flag;
  };
}

describe('teamDraftInterleave', () => {
  test('blends two disjoint rankings, alternating teams', () => {
    const result = teamDraftInterleave(['a1', 'a2', 'a3'], ['b1', 'b2', 'b3'], alwaysA);
    expect(result.map((r) => r.id)).toEqual(['a1', 'b1', 'a2', 'b2', 'a3', 'b3']);
    expect(result.map((r) => r.team)).toEqual(['A', 'B', 'A', 'B', 'A', 'B']);
  });

  test('the coin decides who takes the top slot', () => {
    expect(teamDraftInterleave(['a1'], ['b1'], alwaysA)[0].team).toBe('A');
    expect(teamDraftInterleave(['a1'], ['b1'], alwaysB)[0].team).toBe('B');
  });

  test('keeps the teams within one slot of each other — no position advantage', () => {
    const result = teamDraftInterleave(
      ['a1', 'a2', 'a3', 'a4', 'a5'],
      ['b1', 'b2', 'b3', 'b4', 'b5'],
      alternating(),
    );
    const countA = result.filter((r) => r.team === 'A').length;
    expect(Math.abs(countA - (result.length - countA))).toBeLessThanOrEqual(1);
  });

  test('a document both rankers picked appears once, credited to the drafter', () => {
    const result = teamDraftInterleave(['x', 'a2'], ['x', 'b2'], alwaysA);
    expect(result.map((r) => r.id)).toEqual(['x', 'b2', 'a2']);
    expect(result.filter((r) => r.id === 'x')).toHaveLength(1);
  });

  test('identical rankings produce the shared list with no duplicates', () => {
    const result = teamDraftInterleave(['x', 'y', 'z'], ['x', 'y', 'z'], alternating());
    expect(result.map((r) => r.id)).toEqual(['x', 'y', 'z']);
  });

  test('handles a short ranking on one side', () => {
    const result = teamDraftInterleave(['a1'], ['b1', 'b2', 'b3'], alwaysA);
    expect(result.map((r) => r.id)).toEqual(['a1', 'b1', 'b2', 'b3']);
  });

  test('respects the requested length', () => {
    expect(teamDraftInterleave(['a1', 'a2'], ['b1', 'b2'], alwaysA, 2)).toHaveLength(2);
  });
});

describe('scoreQuery', () => {
  const interleaved = teamDraftInterleave(['a1', 'a2'], ['b1', 'b2'], alwaysA);

  test('credits clicks to the contributing ranker', () => {
    expect(scoreQuery(interleaved, ['b1', 'b2'])).toMatchObject({
      creditA: 0,
      creditB: 2,
      outcome: 'B',
    });
  });

  test('equal credit is a tie', () => {
    expect(scoreQuery(interleaved, ['a1', 'b1']).outcome).toBe('TIE');
  });

  test('no clicks is a tie, not a loss', () => {
    expect(scoreQuery(interleaved, []).outcome).toBe('TIE');
  });

  test('ignores clicks on ids that were never shown', () => {
    expect(scoreQuery(interleaved, ['ghost']).outcome).toBe('TIE');
  });
});

describe('interleavingPreference', () => {
  const outcomes = (a: number, b: number, ties: number): QueryOutcome[] => [
    ...Array<QueryOutcome>(a).fill('A'),
    ...Array<QueryOutcome>(b).fill('B'),
    ...Array<QueryOutcome>(ties).fill('TIE'),
  ];

  test('a clear preference for B is significant', () => {
    const result = interleavingPreference(outcomes(40, 70, 5_000));
    expect(result.preferenceForB).toBeCloseTo(70 / 110, 6);
    expect(result.pValue).toBeLessThan(0.01);
    expect(result.significant).toBe(true);
  });

  test('ties are excluded from the test but reported', () => {
    const result = interleavingPreference(outcomes(40, 70, 5_000));
    expect(result.ties).toBe(5_000);
    expect(result.decisiveQueries).toBe(110);
    expect(result.pValue).toBeCloseTo(binomialTest(70, 110, 0.5), 12);
  });

  test('an even split is not significant', () => {
    const result = interleavingPreference(outcomes(500, 500, 100));
    expect(result.preferenceForB).toBeCloseTo(0.5, 9);
    expect(result.significant).toBe(false);
  });

  test('needs far fewer queries than an A/B test needs users', () => {
    // 60/40 on 200 decisive queries already clears 0.05 — the sensitivity claim
    // that makes interleaving worth the plumbing.
    const result = interleavingPreference(outcomes(80, 120, 0));
    expect(result.significant).toBe(true);
  });

  test('all ties returns a neutral verdict rather than dividing by zero', () => {
    const result = interleavingPreference(outcomes(0, 0, 50));
    expect(result.preferenceForB).toBe(0.5);
    expect(result.pValue).toBe(1);
    expect(result.significant).toBe(false);
  });
});

describe('binomialTest', () => {
  test('matches known exact values', () => {
    expect(binomialTest(5, 10, 0.5)).toBeCloseTo(1, 9);
    expect(binomialTest(0, 10, 0.5)).toBeCloseTo(2 / 1024, 9);
    expect(binomialTest(9, 10, 0.5)).toBeCloseTo(22 / 1024, 9);
  });

  test('handles the degenerate case', () => {
    expect(binomialTest(0, 0)).toBe(1);
    expect(() => binomialTest(5, 2)).toThrow(RangeError);
  });
});
