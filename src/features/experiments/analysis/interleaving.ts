import { binomialTest } from './stats';

/**
 * Team-draft interleaving — the ranking evaluator's power tool.
 *
 * In a normal A/B test of two rankers, user A sees ranking A and user B sees
 * ranking B, and the comparison has to fight every source of between-user
 * variance there is: one user is a power user, another is on a slow phone,
 * another was never going to click anything. Interleaving removes all of it by
 * showing *one* blended list to *every* user and asking which ranker's
 * contributions got clicked. Each query becomes its own paired comparison.
 *
 * The reported sensitivity gain is one to two orders of magnitude: a ranking
 * change that needs weeks of A/B traffic can be called in a day. Two caveats
 * that keep it honest:
 *
 *  - It answers "which ranker do users prefer, click-wise", not "what does this
 *    do to revenue / session length / next-week retention". Interleaving is a
 *    fast *filter*; the winner still goes to a real A/B for the business metric.
 *  - It only applies where you can blend two outputs into one presentation —
 *    rankings, recommendation lists, autocomplete. You cannot interleave a
 *    fraud decision or a credit limit.
 *
 * Team-draft specifically: the two rankers take turns drafting their top
 * remaining document, with a coin flip deciding who picks first at each round.
 * The alternation is what removes position bias — neither ranker
 * systematically gets the higher slots.
 */

export interface InterleavedItem {
  id: string;
  /** Which ranker contributed this slot. */
  team: 'A' | 'B';
  /** 0-based position in the blended list. */
  position: number;
}

/**
 * @param rankingA ranked ids from ranker A (control / production)
 * @param rankingB ranked ids from ranker B (candidate)
 * @param coinFlip injected randomness — returns true when A drafts first this
 *        round. Injected rather than called internally so tests are
 *        deterministic and so production can seed it per query id.
 */
export function teamDraftInterleave(
  rankingA: ReadonlyArray<string>,
  rankingB: ReadonlyArray<string>,
  coinFlip: () => boolean,
  /**
   * Slots to fill. Defaults to the full blend; production passes the real SERP
   * size, which is what the users actually see and therefore what the clicks
   * can be credited against.
   */
  length = rankingA.length + rankingB.length,
): InterleavedItem[] {
  const result: InterleavedItem[] = [];
  const used = new Set<string>();
  let indexA = 0;
  let indexB = 0;

  const nextFrom = (ranking: ReadonlyArray<string>, from: number): number => {
    let i = from;
    while (i < ranking.length && used.has(ranking[i])) i += 1;
    return i;
  };

  while (result.length < length) {
    indexA = nextFrom(rankingA, indexA);
    indexB = nextFrom(rankingB, indexB);
    const aHas = indexA < rankingA.length;
    const bHas = indexB < rankingB.length;
    if (!aHas && !bHas) break;

    // Whoever is behind picks; ties are broken by the coin. This keeps the two
    // teams' slot counts within one of each other, which is the property the
    // analysis assumes.
    const countA = result.filter((r) => r.team === 'A').length;
    const countB = result.length - countA;
    let takeA: boolean;
    if (!bHas) takeA = true;
    else if (!aHas) takeA = false;
    else if (countA < countB) takeA = true;
    else if (countB < countA) takeA = false;
    else takeA = coinFlip();

    if (takeA) {
      const id = rankingA[indexA];
      used.add(id);
      result.push({ id, team: 'A', position: result.length });
    } else {
      const id = rankingB[indexB];
      used.add(id);
      result.push({ id, team: 'B', position: result.length });
    }
  }
  return result;
}

export type QueryOutcome = 'A' | 'B' | 'TIE';

/**
 * Credit the clicks on one impression to the teams and decide who won that
 * query. A query where both teams got equal clicks (including zero) is a tie
 * and carries no information — ties are excluded from the test, not counted as
 * half a win, because they are the overwhelming majority of queries and
 * including them would bury the signal.
 */
export function scoreQuery(
  interleaved: ReadonlyArray<InterleavedItem>,
  clickedIds: ReadonlyArray<string>,
): { creditA: number; creditB: number; outcome: QueryOutcome } {
  const clicked = new Set(clickedIds);
  let creditA = 0;
  let creditB = 0;
  for (const item of interleaved) {
    if (!clicked.has(item.id)) continue;
    if (item.team === 'A') creditA += 1;
    else creditB += 1;
  }
  const outcome: QueryOutcome = creditA > creditB ? 'A' : creditB > creditA ? 'B' : 'TIE';
  return { creditA, creditB, outcome };
}

export interface InterleavingResult {
  winsA: number;
  winsB: number;
  ties: number;
  /**
   * Preference for B among decisive queries, 0..1. 0.5 is indifference; the
   * literature calls this Δ_AB when centred at zero.
   */
  preferenceForB: number;
  /** Exact two-sided binomial (sign) test on the decisive queries. */
  pValue: number;
  significant: boolean;
  decisiveQueries: number;
}

/**
 * Aggregate per-query outcomes into a verdict.
 *
 * The test is a sign test over queries — one query, one vote, regardless of how
 * many clicks it produced. That deliberately throws away magnitude in exchange
 * for robustness: a single user hammering one query cannot swing the result,
 * which is the same clustering problem that ratioMetrics.ts solves for
 * ordinary A/B metrics.
 */
export function interleavingPreference(
  outcomes: ReadonlyArray<QueryOutcome>,
  alpha = 0.05,
): InterleavingResult {
  const winsA = outcomes.filter((o) => o === 'A').length;
  const winsB = outcomes.filter((o) => o === 'B').length;
  const ties = outcomes.length - winsA - winsB;
  const decisive = winsA + winsB;
  const pValue = decisive === 0 ? 1 : binomialTest(winsB, decisive, 0.5);

  return {
    winsA,
    winsB,
    ties,
    preferenceForB: decisive === 0 ? 0.5 : winsB / decisive,
    pValue,
    significant: pValue < alpha,
    decisiveQueries: decisive,
  };
}
