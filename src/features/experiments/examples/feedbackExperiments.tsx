import React from 'react';
import { FeedbackWidget, FeedbackWidgetProps } from '../../feedback';
import { useExperiment } from '../ExperimentProvider';
import { Experiment } from '../types';

/**
 * A real experiment on the feature this repo ships: the feedback widget.
 *
 * Hypothesis — the thank-you toast holds the screen for 2s, which reads as
 * slow on a flow the user has already finished. Cutting it to 1.2s should not
 * hurt comprehension and should raise the share of users who complete a second
 * rating in the same session.
 *
 *   Metric (OEC):  second-rating rate within the session
 *   Guardrails:    NEGATIVE-form submit rate, Trustpilot CTA click-through
 *   Unit:          user id (the widget is only shown to signed-in users)
 *   MDE:           +1pp on a 6% base → see docs/ab-testing.md § "Sizing"
 */
export const THANK_YOU_DELAY = 'feedback-thankyou-delay';

export const thankYouDelayExperiment: Experiment<{ thankYouDelayMs: number }> = {
  key: THANK_YOU_DELAY,
  status: 'RUNNING',
  traffic: 1,
  variants: [
    { key: 'control', weight: 1, payload: { thankYouDelayMs: 2000 } },
    { key: 'snappy', weight: 1, payload: { thankYouDelayMs: 1200 } },
  ],
};

/**
 * A second, *targeted* experiment: send Dutch users to the NL Trustpilot page.
 *
 * Note what targeting buys and what it costs. It removes users who cannot
 * possibly respond to the change (which raises sensitivity), and it makes the
 * result un-generalisable to everyone else (which is the price). Targeting is
 * applied *before* randomisation, so the arms stay comparable — filtering
 * after assignment, on an attribute the treatment can influence, is how you
 * silently break randomisation.
 */
export const TRUSTPILOT_LOCALE = 'feedback-trustpilot-locale';

export const trustpilotLocaleExperiment: Experiment<{ trustpilotUrl: string }> = {
  key: TRUSTPILOT_LOCALE,
  status: 'RUNNING',
  traffic: 0.5,
  audience: (ctx) => ctx.country === 'NL',
  variants: [
    { key: 'control', weight: 1, payload: { trustpilotUrl: 'https://www.trustpilot.com/review/bunq.com' } },
    { key: 'localised', weight: 1, payload: { trustpilotUrl: 'https://nl.trustpilot.com/review/bunq.com' } },
  ],
};

export const feedbackExperiments = [
  thankYouDelayExperiment as Experiment<unknown>,
  trustpilotLocaleExperiment as Experiment<unknown>,
];

export type ExperimentalFeedbackWidgetProps = Omit<FeedbackWidgetProps, 'thankYouDelayMs'>;

/**
 * The wiring, and the one line that decides whether the readout is any good:
 *
 *     useExperiment(THANK_YOU_DELAY, { exposed: open })
 *
 * The widget is mounted on every page but visible almost nowhere. Exposing on
 * mount would log ~50× more units than can possibly see the change, and every
 * one of them contributes a zero to the difference between arms — the measured
 * effect shrinks towards nothing while the traffic bill stays the same. Gating
 * exposure on "the modal is actually open" is what keeps the estimate honest.
 *
 * Keeping this wrapper separate from FeedbackWidget is deliberate too: the
 * widget stays a pure, experiment-free component that tests can drive
 * directly, and the experiment lives in one file you can delete on clean-up
 * day. Experiments are temporary; components are not.
 */
export function ExperimentalFeedbackWidget({
  open,
  ...rest
}: ExperimentalFeedbackWidgetProps): JSX.Element {
  const { payload } = useExperiment<{ thankYouDelayMs: number }>(THANK_YOU_DELAY, {
    exposed: open,
  });

  return (
    <FeedbackWidget open={open} thankYouDelayMs={payload?.thankYouDelayMs ?? 2000} {...rest} />
  );
}
