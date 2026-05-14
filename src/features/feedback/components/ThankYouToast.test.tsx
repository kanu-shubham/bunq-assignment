import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { ThankYouToast } from './ThankYouToast';

describe('<ThankYouToast />', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    act(() => { jest.runOnlyPendingTimers(); });
    jest.useRealTimers();
  });

  test('renders a polite status with the thank-you message', () => {
    render(<ThankYouToast active onDone={jest.fn()} titleId="ty" />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByText(/thanks for your feedback/i)).toHaveAttribute('id', 'ty');
  });

  test('calls onDone after the default 2000ms delay when active', () => {
    const onDone = jest.fn();
    render(<ThankYouToast active onDone={onDone} titleId="t" />);
    expect(onDone).not.toHaveBeenCalled();

    act(() => { jest.advanceTimersByTime(1999); });
    expect(onDone).not.toHaveBeenCalled();

    act(() => { jest.advanceTimersByTime(1); });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  test('honors a custom delay', () => {
    const onDone = jest.fn();
    render(<ThankYouToast active onDone={onDone} delay={500} titleId="t" />);

    act(() => { jest.advanceTimersByTime(499); });
    expect(onDone).not.toHaveBeenCalled();

    act(() => { jest.advanceTimersByTime(1); });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  test('does not fire onDone when not active', () => {
    const onDone = jest.fn();
    render(<ThankYouToast active={false} onDone={onDone} titleId="t" />);
    act(() => { jest.advanceTimersByTime(5000); });
    expect(onDone).not.toHaveBeenCalled();
  });

  test('decorative svg is hidden from assistive tech', () => {
    const { container } = render(<ThankYouToast active onDone={jest.fn()} titleId="t" />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg).toHaveAttribute('focusable', 'false');
  });
});
