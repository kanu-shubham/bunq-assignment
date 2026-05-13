import { useEffect, useRef } from 'react';

export interface AutoDismissOptions {
  active: boolean;
  delay?: number;
}

/**
 * Fires `onDismiss` after `delay` ms while `active` is true. The handler
 * is read through a ref so updates to it don't reset the timer.
 */
export function useAutoDismiss(onDismiss: () => void, { active, delay = 2000 }: AutoDismissOptions): void {
  const handlerRef = useRef(onDismiss);
  handlerRef.current = onDismiss;

  useEffect(() => {
    if (!active) return undefined;
    const id = window.setTimeout(() => handlerRef.current(), delay);
    return () => window.clearTimeout(id);
  }, [active, delay]);
}
