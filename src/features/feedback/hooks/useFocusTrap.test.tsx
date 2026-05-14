import React, { useRef } from 'react';
import { render, fireEvent } from '@testing-library/react';
import { useFocusTrap } from './useFocusTrap';

function Probe({
  enabled = true,
  children,
}: {
  enabled?: boolean;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, enabled);
  return (
    <div ref={ref} tabIndex={-1} data-testid="container">
      {children}
    </div>
  );
}

describe('useFocusTrap', () => {
  test('focuses the first focusable element on mount', () => {
    render(
      <Probe>
        <button data-testid="first">first</button>
        <button data-testid="last">last</button>
      </Probe>,
    );
    expect(document.activeElement).toBe(document.querySelector('[data-testid="first"]'));
  });

  test('focuses the container itself when no focusables exist', () => {
    render(
      <Probe>
        <span>no buttons</span>
      </Probe>,
    );
    const container = document.querySelector('[data-testid="container"]');
    expect(document.activeElement).toBe(container);
  });

  test('Tab from the last focusable wraps to the first', () => {
    render(
      <Probe>
        <button data-testid="first">first</button>
        <button data-testid="last">last</button>
      </Probe>,
    );
    const last = document.querySelector<HTMLButtonElement>('[data-testid="last"]')!;
    last.focus();
    expect(document.activeElement).toBe(last);

    fireEvent.keyDown(document.querySelector('[data-testid="container"]')!, { key: 'Tab' });
    expect(document.activeElement).toBe(document.querySelector('[data-testid="first"]'));
  });

  test('Shift+Tab from the first focusable wraps to the last', () => {
    render(
      <Probe>
        <button data-testid="first">first</button>
        <button data-testid="last">last</button>
      </Probe>,
    );
    const first = document.querySelector<HTMLButtonElement>('[data-testid="first"]')!;
    first.focus();

    fireEvent.keyDown(document.querySelector('[data-testid="container"]')!, {
      key: 'Tab',
      shiftKey: true,
    });
    expect(document.activeElement).toBe(document.querySelector('[data-testid="last"]'));
  });

  test('non-Tab keys are ignored', () => {
    render(
      <Probe>
        <button data-testid="first">first</button>
        <button data-testid="last">last</button>
      </Probe>,
    );
    const last = document.querySelector<HTMLButtonElement>('[data-testid="last"]')!;
    last.focus();

    fireEvent.keyDown(document.querySelector('[data-testid="container"]')!, { key: 'Enter' });
    expect(document.activeElement).toBe(last);
  });

  test('does not trap when disabled', () => {
    const previous = document.createElement('button');
    document.body.appendChild(previous);
    previous.focus();

    render(
      <Probe enabled={false}>
        <button data-testid="first">first</button>
      </Probe>,
    );

    expect(document.activeElement).toBe(previous);
    document.body.removeChild(previous);
  });

  test('restores focus to the previously focused element on unmount', () => {
    const previous = document.createElement('button');
    previous.setAttribute('data-testid', 'previous');
    document.body.appendChild(previous);
    previous.focus();

    const { unmount } = render(
      <Probe>
        <button data-testid="first">first</button>
      </Probe>,
    );
    expect(document.activeElement).toBe(document.querySelector('[data-testid="first"]'));

    unmount();
    expect(document.activeElement).toBe(previous);
    document.body.removeChild(previous);
  });

  test('skips disabled buttons when picking focusables', () => {
    render(
      <Probe>
        <button data-testid="first" disabled>disabled</button>
        <button data-testid="second">second</button>
      </Probe>,
    );
    expect(document.activeElement).toBe(document.querySelector('[data-testid="second"]'));
  });
});
