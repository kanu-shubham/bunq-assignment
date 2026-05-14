import React from 'react';
import { render, act } from '@testing-library/react';
import { useAutoDismiss, AutoDismissOptions } from './useAutoDismiss';

function Probe({ onDismiss, options }: { onDismiss: () => void; options: AutoDismissOptions }) {
  useAutoDismiss(onDismiss, options);
  return null;
}

describe('useAutoDismiss', () => {
  beforeEach(() => { jest.useFakeTimers(); });
  afterEach(() => {
    act(() => { jest.runOnlyPendingTimers(); });
    jest.useRealTimers();
  });

  test('fires onDismiss after the default 2000ms when active', () => {
    const onDismiss = jest.fn();
    render(<Probe onDismiss={onDismiss} options={{ active: true }} />);

    act(() => { jest.advanceTimersByTime(1999); });
    expect(onDismiss).not.toHaveBeenCalled();

    act(() => { jest.advanceTimersByTime(1); });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test('honors a custom delay', () => {
    const onDismiss = jest.fn();
    render(<Probe onDismiss={onDismiss} options={{ active: true, delay: 250 }} />);
    act(() => { jest.advanceTimersByTime(250); });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test('does not fire when inactive', () => {
    const onDismiss = jest.fn();
    render(<Probe onDismiss={onDismiss} options={{ active: false, delay: 100 }} />);
    act(() => { jest.advanceTimersByTime(1000); });
    expect(onDismiss).not.toHaveBeenCalled();
  });

  test('clears the timer when becoming inactive', () => {
    const onDismiss = jest.fn();
    const { rerender } = render(<Probe onDismiss={onDismiss} options={{ active: true, delay: 1000 }} />);
    act(() => { jest.advanceTimersByTime(500); });

    rerender(<Probe onDismiss={onDismiss} options={{ active: false, delay: 1000 }} />);
    act(() => { jest.advanceTimersByTime(2000); });
    expect(onDismiss).not.toHaveBeenCalled();
  });

  test('updating the handler does not reset the timer', () => {
    const first = jest.fn();
    const second = jest.fn();
    const { rerender } = render(<Probe onDismiss={first} options={{ active: true, delay: 1000 }} />);

    act(() => { jest.advanceTimersByTime(500); });
    rerender(<Probe onDismiss={second} options={{ active: true, delay: 1000 }} />);
    act(() => { jest.advanceTimersByTime(500); });

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  test('restarts when delay changes', () => {
    const onDismiss = jest.fn();
    const { rerender } = render(<Probe onDismiss={onDismiss} options={{ active: true, delay: 1000 }} />);
    act(() => { jest.advanceTimersByTime(900); });

    rerender(<Probe onDismiss={onDismiss} options={{ active: true, delay: 200 }} />);
    act(() => { jest.advanceTimersByTime(199); });
    expect(onDismiss).not.toHaveBeenCalled();

    act(() => { jest.advanceTimersByTime(1); });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
