import React, { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';
import { useStableId } from './hooks/useStableId';
import { Modal, ModalVariant } from './components/Modal';
import { RatingPrompt } from './components/RatingPrompt';
import { NegativeFeedbackForm } from './components/NegativeFeedbackForm';
import { ThankYouToast } from './components/ThankYouToast';
import { TrustpilotPrompt } from './components/TrustpilotPrompt';
import {
  FeedbackState,
  RATING,
  STATUS,
  initialState,
  reducer,
} from './state/feedbackMachine';
import {
  FeedbackPayload,
  submitFeedback as defaultSubmit,
} from './services/feedbackService';

export interface FeedbackWidgetProps {
  /** Controlled visibility flag. */
  open: boolean;
  /** Fires exactly once per non-CLOSED → CLOSED transition. */
  onClose?: () => void;
  /** DI seam for the network call (override in tests). */
  submitFeedback?: (payload: FeedbackPayload) => Promise<unknown>;
  /** Link target for the STELLAR review CTA. */
  trustpilotUrl?: string;
  /** Auto-dismiss delay for the thank-you toast. */
  thankYouDelayMs?: number;
}

const initFromOpen = (open: boolean): FeedbackState => ({
  ...initialState,
  status: open ? STATUS.RATING : STATUS.CLOSED,
});

export function FeedbackWidget({
  open,
  onClose,
  submitFeedback = defaultSubmit,
  trustpilotUrl,
  thankYouDelayMs = 2000,
}: FeedbackWidgetProps): JSX.Element | null {
  const [state, dispatch] = useReducer(reducer, open, initFromOpen);

  // Edge-trigger on `open` prop transitions only.
  const prevOpenRef = useRef(open);
  useEffect(() => {
    const prev = prevOpenRef.current;
    prevOpenRef.current = open;
    if (open && !prev) dispatch({ type: 'OPEN' });
    else if (!open && prev) dispatch({ type: 'CLOSE' });
  }, [open]);

  const close = useCallback(() => {
    dispatch({ type: 'CLOSE' });
  }, []);

  const handleRate = useCallback((rating: typeof RATING[keyof typeof RATING]) => {
    dispatch({ type: 'RATE', rating });
  }, []);

  const handleSubmit = useCallback(
    async (comment: string) => {
      dispatch({ type: 'SUBMIT_START' });
      try {
        await submitFeedback({ rating: RATING.NEGATIVE, comment });
        dispatch({ type: 'SUBMIT_SUCCESS', comment });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Submission failed';
        dispatch({ type: 'SUBMIT_ERROR', error: message });
      }
    },
    [submitFeedback],
  );

  const handleThankYouDone = useCallback(() => {
    dispatch({ type: 'THANK_YOU_DONE' });
  }, []);

  // Notify parent exactly once per non-CLOSED → CLOSED transition.
  const wasOpenRef = useRef(state.status !== STATUS.CLOSED);
  useEffect(() => {
    if (state.status !== STATUS.CLOSED) {
      wasOpenRef.current = true;
    } else if (wasOpenRef.current) {
      wasOpenRef.current = false;
      onClose?.();
    }
  }, [state.status, onClose]);

  const titleId = useStableId('fb-title');
  const step = state.status;

  const content = useMemo(() => {
    switch (step) {
      case STATUS.RATING:
        return <RatingPrompt onRate={handleRate} titleId={titleId} />;
      case STATUS.NEGATIVE_FORM:
      case STATUS.SUBMITTING:
        return (
          <NegativeFeedbackForm
            onSubmit={handleSubmit}
            submitting={step === STATUS.SUBMITTING}
            error={state.error}
            titleId={titleId}
          />
        );
      case STATUS.THANK_YOU:
        return (
          <ThankYouToast
            active
            onDone={handleThankYouDone}
            delay={thankYouDelayMs}
            titleId={titleId}
          />
        );
      case STATUS.TRUSTPILOT:
        return <TrustpilotPrompt trustpilotUrl={trustpilotUrl} onClose={close} titleId={titleId} />;
      default:
        return null;
    }
  }, [
    step,
    state.error,
    titleId,
    handleRate,
    handleSubmit,
    handleThankYouDone,
    thankYouDelayMs,
    trustpilotUrl,
    close,
  ]);

  if (step === STATUS.CLOSED) return null;

  const variant: ModalVariant =
    step === STATUS.THANK_YOU ? 'blue'
      : step === STATUS.TRUSTPILOT ? 'review'
      : 'dark';

  const dismissable = step !== STATUS.THANK_YOU && step !== STATUS.SUBMITTING;

  return (
    <Modal
      open
      onClose={close}
      labelledBy={titleId}
      variant={variant}
      dismissable={dismissable}
      showClose={step !== STATUS.THANK_YOU}
      role={step === STATUS.THANK_YOU ? 'status' : 'dialog'}
    >
      {content}
    </Modal>
  );
}
