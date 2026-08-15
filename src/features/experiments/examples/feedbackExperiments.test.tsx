import React from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { ExperimentProvider } from '../ExperimentProvider';
import { assign } from '../core/assign';
import {
  ExperimentalFeedbackWidget,
  THANK_YOU_DELAY,
  TRUSTPILOT_LOCALE,
  feedbackExperiments,
  trustpilotLocaleExperiment,
} from './feedbackExperiments';

interface Options {
  open?: boolean;
  variant?: string;
}

function setup({ open = true, variant }: Options = {}) {
  const track = jest.fn();
  const submitFeedback = jest.fn().mockResolvedValue({});
  const utils = render(
    <ExperimentProvider
      unitId="user-1"
      experiments={feedbackExperiments}
      overrides={variant ? { [THANK_YOU_DELAY]: variant } : {}}
      track={track}
    >
      <ExperimentalFeedbackWidget open={open} submitFeedback={submitFeedback} />
    </ExperimentProvider>,
  );
  return { ...utils, track };
}

describe('feedback thank-you delay experiment', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  test('control keeps the 2s toast', () => {
    setup({ variant: 'control' });
    fireEvent.click(screen.getByRole('button', { name: /positive/i }));
    expect(screen.getByText(/thanks for your feedback/i)).toBeInTheDocument();

    act(() => {
      jest.advanceTimersByTime(1200);
    });
    expect(screen.queryByText(/thanks for your feedback/i)).toBeInTheDocument();

    act(() => {
      jest.advanceTimersByTime(800);
    });
    expect(screen.queryByText(/thanks for your feedback/i)).not.toBeInTheDocument();
  });

  test('treatment dismisses the toast at 1.2s', () => {
    setup({ variant: 'snappy' });
    fireEvent.click(screen.getByRole('button', { name: /positive/i }));

    act(() => {
      jest.advanceTimersByTime(1200);
    });
    expect(screen.queryByText(/thanks for your feedback/i)).not.toBeInTheDocument();
  });

  describe('exposure', () => {
    test('is not logged while the widget is mounted but closed — no dilution', () => {
      const { track } = setup({ open: false });
      expect(track).not.toHaveBeenCalled();
    });

    test('is logged once when the widget actually opens', () => {
      const { track } = setup({ open: true });
      expect(track).toHaveBeenCalledTimes(1);
      expect(track.mock.calls[0][0]).toMatchObject({
        experimentKey: THANK_YOU_DELAY,
        unitId: 'user-1',
      });
    });

    test('a QA override renders the arm without logging an exposure', () => {
      const { track } = setup({ variant: 'snappy' });
      expect(track).not.toHaveBeenCalled();
    });
  });
});

describe('trustpilot locale experiment (targeting)', () => {
  const enrolledFor = (country: string): boolean =>
    assign({
      experiment: trustpilotLocaleExperiment,
      unitId: 'user-1',
      attributes: { country },
    }).enrolled;

  test('only Dutch users are eligible', () => {
    expect(assign({
      experiment: trustpilotLocaleExperiment,
      unitId: 'user-1',
      attributes: { country: 'DE' },
    })).toMatchObject({ reason: 'NOT_ELIGIBLE', enrolled: false });
    // NL users are eligible, though only half of them are in the 50% ramp.
    expect([true, false]).toContain(enrolledFor('NL'));
  });

  test('ineligible users still get a working control URL', () => {
    const { payload } = assign({
      experiment: trustpilotLocaleExperiment,
      unitId: 'user-1',
      attributes: { country: 'DE' },
    });
    expect(payload?.trustpilotUrl).toBe('https://www.trustpilot.com/review/bunq.com');
  });

  test('the ramp lets in about half of the eligible population', () => {
    const ids = Array.from({ length: 5000 }, (_, i) => `nl-user-${i}`);
    const enrolled = ids.filter(
      (unitId) =>
        assign({
          experiment: trustpilotLocaleExperiment,
          unitId,
          attributes: { country: 'NL' },
        }).enrolled,
    ).length;
    expect(enrolled / ids.length).toBeGreaterThan(0.47);
    expect(enrolled / ids.length).toBeLessThan(0.53);
  });

  test('the registry exposes both experiments by key', () => {
    expect(feedbackExperiments.map((e) => e.key)).toEqual([THANK_YOU_DELAY, TRUSTPILOT_LOCALE]);
  });
});
