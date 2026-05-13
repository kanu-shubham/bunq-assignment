import React, { useCallback, useState } from 'react';
import { FeedbackWidget } from './features/feedback';
import type { FeedbackPayload } from './features/feedback';
import './App.css';

/**
 * Demo submit implementation. The real service POSTs to /api/feedback,
 * which doesn't exist in this standalone demo (dev server returns 405),
 * so we inject a fake here that just resolves after a small delay to
 * exercise the SUBMITTING → THANK_YOU transition end-to-end.
 *
 * In a real bunq app this prop is omitted and the production
 * submitFeedback service is used.
 */
function demoSubmit(payload: FeedbackPayload): Promise<unknown> {
  // eslint-disable-next-line no-console
  console.info('[feedback demo] submitted:', payload);
  return new Promise((resolve) => setTimeout(resolve, 400));
}

export default function App(): JSX.Element {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const open = useCallback(() => setFeedbackOpen(true), []);
  const close = useCallback(() => setFeedbackOpen(false), []);

  return (
    <div className="app">
      <main className="app__shell">
        <h1 className="app__title">Feature Rating Demo</h1>
        <p className="app__lede">
          Click the button below to open the feedback popup.
        </p>
        <button
          type="button"
          className="app__cta"
          onClick={open}
          aria-haspopup="dialog"
        >
          How would you rate this feature?
        </button>
      </main>
      <FeedbackWidget
        open={feedbackOpen}
        onClose={close}
        submitFeedback={demoSubmit}
      />
    </div>
  );
}
