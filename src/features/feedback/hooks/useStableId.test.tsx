import React, { useEffect } from 'react';
import { render } from '@testing-library/react';
import { useStableId } from './useStableId';

function Probe({ onId, prefix }: { onId: (id: string) => void; prefix?: string }) {
  const id = useStableId(prefix);
  useEffect(() => { onId(id); });
  return <span data-testid="id">{id}</span>;
}

describe('useStableId', () => {
  test('returns a stable id across re-renders', () => {
    const seen: string[] = [];
    const { rerender } = render(<Probe onId={(id) => seen.push(id)} />);
    rerender(<Probe onId={(id) => seen.push(id)} />);
    rerender(<Probe onId={(id) => seen.push(id)} />);
    expect(new Set(seen).size).toBe(1);
  });

  test('uses the default prefix when none is provided', () => {
    let id = '';
    render(<Probe onId={(v) => { id = v; }} />);
    expect(id).toMatch(/^fb-fb-\d+$/);
  });

  test('honors a custom prefix', () => {
    let id = '';
    render(<Probe onId={(v) => { id = v; }} prefix="custom" />);
    expect(id).toMatch(/^custom-fb-\d+$/);
  });

  test('produces unique ids across separate instances', () => {
    const ids: string[] = [];
    render(<Probe onId={(v) => ids.push(v)} />);
    render(<Probe onId={(v) => ids.push(v)} />);
    render(<Probe onId={(v) => ids.push(v)} />);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
