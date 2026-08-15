/**
 * Deterministic bucketing.
 *
 * Requirements a bucketing function has to meet, in order of how badly things
 * break when you get them wrong:
 *
 *  1. **Deterministic.** Same (salt, unitId) → same bucket, on every device,
 *     every reload, every release. `Math.random()` re-rolls on each render and
 *     silently destroys the experiment.
 *  2. **Independent across experiments.** Salting with the experiment key means
 *     being in `treatment` of experiment A tells you nothing about experiment B.
 *     Without the salt, every experiment splits the population the same way and
 *     their effects get confounded.
 *  3. **Uniform.** Buckets spread evenly over [0,1), or your 50/50 isn't 50/50.
 *  4. **Cheap.** It runs on every render of every experiment. FNV-1a is a few
 *     ns; SHA-256 via WebCrypto is async and would force the UI to wait.
 *
 * FNV-1a is not a cryptographic hash — an attacker can find inputs landing in a
 * chosen bucket. That is fine for UI experiments, and not fine for anything
 * where the variant is a secret or a privilege. Bucket server-side for those.
 */

const FNV_OFFSET_BASIS = 0x811c9dc5;
const FNV_PRIME = 0x01000193;
const UINT32_RANGE = 0x1_0000_0000;

/** 32-bit FNV-1a over UTF-16 code units. Returns an unsigned 32-bit int. */
export function fnv1a32(input: string): number {
  let hash = FNV_OFFSET_BASIS;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    // Math.imul keeps the multiply in 32-bit space; `hash * FNV_PRIME` would
    // exceed 2^53 and lose the low bits we actually care about.
    hash = Math.imul(hash, FNV_PRIME);
  }
  return hash >>> 0;
}

/**
 * Murmur3 finaliser (fmix32) — an avalanche step, not a hash.
 *
 * Needed because FNV-1a alone is *not* uniform enough for the inputs we
 * actually have. Real unit ids are sequential (`user-1`, `user-2`, …), and on
 * short, near-identical strings FNV-1a leaves structure in the high bits —
 * which is precisely the region `hashFraction` divides on. Measured over 20k
 * sequential ids, raw FNV-1a deciles run from 1,598 to 2,398 against an
 * expectation of 2,000 — up to 9.5σ off. With fmix32 the worst decile is 1.3σ
 * off. A skewed bucketer produces a skewed split, and a skewed split shows up
 * as an SRM you cannot debug from the dashboard.
 */
function fmix32(h: number): number {
  let x = h;
  x ^= x >>> 16;
  x = Math.imul(x, 0x85ebca6b);
  x ^= x >>> 13;
  x = Math.imul(x, 0xc2b2ae35);
  x ^= x >>> 16;
  return x >>> 0;
}

/** FNV-1a + avalanche. This is what everything else buckets on. */
export const hash32 = (input: string): number => fmix32(fnv1a32(input));

/**
 * Map (salt, unitId) into [0, 1).
 *
 * The `:` separator prevents the classic collision where ('ab', 'cd') and
 * ('a', 'bcd') concatenate to the same string.
 */
export function hashFraction(salt: string, unitId: string): number {
  return hash32(`${salt}:${unitId}`) / UINT32_RANGE;
}

/**
 * Two *independent* draws per experiment: one decides "are you in the ramp at
 * all", one decides "which arm". Deriving both from a single draw is the bug
 * that makes ramping unsafe — with one draw, moving traffic from 10% → 20%
 * reshuffles which side of the variant boundary the newcomers land on and can
 * move already-enrolled users between arms. With two, the variant draw never
 * changes when traffic changes, so enrolled users are sticky for life.
 */
export const trafficBucket = (salt: string, unitId: string): number =>
  hashFraction(`${salt}#traffic`, unitId);

export const variantBucket = (salt: string, unitId: string): number =>
  hashFraction(`${salt}#variant`, unitId);

export const __test__ = { UINT32_RANGE };
