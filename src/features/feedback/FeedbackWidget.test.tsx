import React from 'react';
import { render, screen, fireEvent, act, RenderResult } from '@testing-library/react';
import { FeedbackWidget, FeedbackWidgetProps } from './FeedbackWidget';

interface SetupResult extends RenderResult {
  onClose: jest.Mock;
  submitFeedback: jest.Mock;
}

function setup(props: Partial<FeedbackWidgetProps> = {}): SetupResult {
  const onClose = jest.fn();
  const submitFeedback = (props.submitFeedback as jest.Mock | undefined) ?? jest.fn().mockResolvedValue({});
  const utils = render(
    <FeedbackWidget open onClose={onClose} submitFeedback={submitFeedback} {...props} />,
  );
  return { ...utils, onClose, submitFeedback };
}

describe('<FeedbackWidget />', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    act(() => { jest.runOnlyPendingTimers(); });
    jest.useRealTimers();
  });

  test('renders the rating prompt when opened', () => {
    setup();
    expect(screen.getByRole('heading', { name: /how would you rate/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /negative/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /positive/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /stellar/i })).toBeInTheDocument();
  });

  test('POSITIVE → thank you → closes after 2s', () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByRole('button', { name: /positive/i }));
    expect(screen.getByText(/thanks for your feedback/i)).toBeInTheDocument();
    act(() => { jest.advanceTimersByTime(2000); });
    expect(onClose).toHaveBeenCalled();
  });

  test('STELLAR → thank you → trustpilot prompt', () => {
    setup({ trustpilotUrl: 'https://example.com/r' });
    fireEvent.click(screen.getByRole('button', { name: /stellar/i }));
    expect(screen.getByText(/thanks for your feedback/i)).toBeInTheDocument();
    act(() => { jest.advanceTimersByTime(2000); });
    expect(screen.getByText(/enjoying bunq/i)).toBeInTheDocument();
    const cta = screen.getByTestId('fb-trustpilot-cta');
    expect(cta).toHaveAttribute('href', 'https://example.com/r');
    expect(cta).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  test('NEGATIVE → submit empty is disabled, then submits and shows thank you', async () => {
    const submitFeedback = jest.fn().mockResolvedValue({});
    const { onClose } = setup({ submitFeedback });

    fireEvent.click(screen.getByRole('button', { name: /negative/i }));
    expect(screen.getByText(/how can we make things better/i)).toBeInTheDocument();

    const submit = screen.getByTestId('fb-negative-submit');
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId('fb-negative-text'), { target: { value: '  bug  ' } });
    expect(submit).not.toBeDisabled();

    await act(async () => {
      fireEvent.click(submit);
    });

    expect(submitFeedback).toHaveBeenCalledWith({ rating: 'NEGATIVE', comment: 'bug' });
    expect(screen.getByText(/thanks for your feedback/i)).toBeInTheDocument();

    act(() => { jest.advanceTimersByTime(2000); });
    expect(onClose).toHaveBeenCalled();
  });

  test('NEGATIVE submission announces "Submitting" to screen readers while pending', async () => {
    // Hold the submit promise open so we can inspect the in-flight state.
    let resolve!: () => void;
    const submitFeedback = jest.fn().mockImplementation(
      () => new Promise<void>((r) => { resolve = r; }),
    );
    setup({ submitFeedback });

    fireEvent.click(screen.getByRole('button', { name: /negative/i }));
    fireEvent.change(screen.getByTestId('fb-negative-text'), { target: { value: 'bug' } });
    fireEvent.click(screen.getByTestId('fb-negative-submit'));

    const live = screen.getByTestId('fb-negative-live');
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(live).toHaveTextContent(/submitting your feedback/i);
    expect(screen.getByTestId('fb-negative-text')).toBeDisabled();

    await act(async () => { resolve(); });
  });

  test('NEGATIVE submission failure surfaces error and stays on form', async () => {
    const submitFeedback = jest.fn().mockRejectedValue(new Error('network down'));
    setup({ submitFeedback });

    fireEvent.click(screen.getByRole('button', { name: /negative/i }));
    fireEvent.change(screen.getByTestId('fb-negative-text'), { target: { value: 'bad' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('fb-negative-submit'));
    });

    expect(await screen.findByRole('alert')).toHaveTextContent(/network down/i);
    expect(screen.getByText(/how can we make things better/i)).toBeInTheDocument();
  });

  test('close button dismisses widget', () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByTestId('fb-close'));
    expect(onClose).toHaveBeenCalled();
  });

  test('ESC dismisses while on RATING', () => {
    const { onClose } = setup();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
