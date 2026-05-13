import { useRef } from 'react';

let counter = 0;
const next = (): string => `fb-${++counter}`;

/**
 * Stable per-instance id. Project targets React 17, so `useId` isn't
 * available; we mint our own. Not collision-safe across SSR — we don't SSR.
 */
export function useStableId(prefix = 'fb'): string {
  const ref = useRef<string | null>(null);
  if (ref.current === null) ref.current = `${prefix}-${next()}`;
  return ref.current;
}
