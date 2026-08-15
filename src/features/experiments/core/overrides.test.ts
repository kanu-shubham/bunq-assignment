import {
  OverrideStorage,
  STORAGE_KEY,
  parseOverridesFromSearch,
  resolveOverrides,
} from './overrides';

function fakeStorage(initial: Record<string, string> = {}): OverrideStorage & { data: Record<string, string> } {
  const data = { ...initial };
  return {
    data,
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => {
      data[key] = value;
    },
  };
}

describe('parseOverridesFromSearch', () => {
  test('reads ab.<key>=<variant> pairs', () => {
    const { overrides } = parseOverridesFromSearch('?ab.rating-copy=treatment&utm_source=email');
    expect(overrides).toEqual({ 'rating-copy': 'treatment' });
  });

  test('an empty value clears one experiment', () => {
    const { cleared } = parseOverridesFromSearch('?ab.rating-copy=');
    expect(cleared).toEqual(['rating-copy']);
  });

  test('ab.reset clears everything', () => {
    expect(parseOverridesFromSearch('?ab.reset=1').resetAll).toBe(true);
  });

  test('ignores unrelated params and a bare prefix', () => {
    const { overrides } = parseOverridesFromSearch('?ab.=x&foo=bar');
    expect(overrides).toEqual({});
  });
});

describe('resolveOverrides', () => {
  test('merges storage with the URL, URL wins', () => {
    const storage = fakeStorage({ [STORAGE_KEY]: JSON.stringify({ a: 'v1', b: 'v1' }) });
    expect(resolveOverrides('?ab.a=v2', storage)).toEqual({ a: 'v2', b: 'v1' });
  });

  test('persists the merged result so it survives navigation', () => {
    const storage = fakeStorage();
    resolveOverrides('?ab.rating-copy=treatment', storage);
    expect(JSON.parse(storage.data[STORAGE_KEY])).toEqual({ 'rating-copy': 'treatment' });
  });

  test('reset drops persisted overrides', () => {
    const storage = fakeStorage({ [STORAGE_KEY]: JSON.stringify({ a: 'v1' }) });
    expect(resolveOverrides('?ab.reset=1', storage)).toEqual({});
  });

  test('survives corrupt storage', () => {
    const storage = fakeStorage({ [STORAGE_KEY]: 'not json{' });
    expect(resolveOverrides('?ab.a=v1', storage)).toEqual({ a: 'v1' });
  });

  test('drops non-string values from storage', () => {
    const storage = fakeStorage({ [STORAGE_KEY]: JSON.stringify({ a: 1, b: 'v1' }) });
    expect(resolveOverrides('', storage)).toEqual({ b: 'v1' });
  });

  test('works with no storage at all (SSR / blocked cookies)', () => {
    expect(resolveOverrides('?ab.a=v1')).toEqual({ a: 'v1' });
  });

  test('a throwing storage never breaks the app', () => {
    const hostile: OverrideStorage = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('quota');
      },
    };
    expect(resolveOverrides('?ab.a=v1', hostile)).toEqual({ a: 'v1' });
  });
});
