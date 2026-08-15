/**
 * Forced-variant plumbing: how a human pins an arm to look at it.
 *
 * Every experiment framework needs this and every team underestimates it.
 * Without it, reviewing a treatment means clearing cookies until the hash goes
 * your way, and a designer signing off on "variant B" has no way to open
 * variant B. Two sources, in precedence order:
 *
 *   1. `?ab.<experimentKey>=<variantKey>` in the URL — shareable in a ticket.
 *   2. Persisted overrides (localStorage) — survives navigation.
 *
 * Reading the URL also *writes* the persisted copy, so a single link pins the
 * arm for the whole session. `?ab.<key>=` (empty) clears one; `?ab.reset=1`
 * clears all.
 *
 * Forced units are never `enrolled` (see assign.ts), so none of this can
 * contaminate the readout — a property worth stating out loud in review,
 * because "we pinned ourselves into treatment and it showed up in the numbers"
 * is a real way experiments get invalidated.
 */

export type Overrides = Readonly<Record<string, string>>;

const PREFIX = 'ab.';
const RESET_KEY = 'ab.reset';
export const STORAGE_KEY = 'ab.overrides';

/** Minimal surface of localStorage we depend on — trivially fake-able in tests. */
export interface OverrideStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function parseOverridesFromSearch(search: string): {
  overrides: Overrides;
  cleared: string[];
  resetAll: boolean;
} {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const overrides: Record<string, string> = {};
  const cleared: string[] = [];
  let resetAll = false;

  params.forEach((value, key) => {
    if (key === RESET_KEY) {
      resetAll = true;
      return;
    }
    if (!key.startsWith(PREFIX)) return;
    const experimentKey = key.slice(PREFIX.length);
    if (!experimentKey) return;
    if (value === '') cleared.push(experimentKey);
    else overrides[experimentKey] = value;
  });

  return { overrides, cleared, resetAll };
}

function readStored(storage: OverrideStorage | undefined): Overrides {
  if (!storage) return {};
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    // Drop anything non-string rather than trusting whatever is in storage.
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(
        (entry): entry is [string, string] => typeof entry[1] === 'string',
      ),
    );
  } catch {
    // Corrupt JSON, disabled storage, Safari private mode — never break the app
    // over a debug feature.
    return {};
  }
}

/**
 * Merge stored + URL overrides, persisting the result. Pure w.r.t. its inputs:
 * pass a fake storage in tests, `window.localStorage` in the app.
 */
export function resolveOverrides(
  search: string,
  storage?: OverrideStorage,
): Overrides {
  const { overrides, cleared, resetAll } = parseOverridesFromSearch(search);
  const merged: Record<string, string> = resetAll ? {} : { ...readStored(storage) };

  for (const key of cleared) delete merged[key];
  Object.assign(merged, overrides);

  if (storage) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(merged));
    } catch {
      /* storage full or blocked — the in-memory result still works */
    }
  }
  return merged;
}
