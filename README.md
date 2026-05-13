# Feedback Widget

Feature-rating popup for the bunq frontend assignment.

**Flow** — a finite-state machine in [`src/features/feedback/state/feedbackMachine.ts`](./src/features/feedback/state/feedbackMachine.ts):
`CLOSED → RATING → { NEGATIVE_FORM → SUBMITTING → THANK_YOU | THANK_YOU } → { CLOSED | TRUSTPILOT }`.
`Action` is a discriminated union; the reducer's `default` holds `const _: never = action`, so a new variant without a case is a compile error.

**DI seam** — the widget accepts a `submitFeedback` prop (typed `(p: FeedbackPayload) => Promise<unknown>`). Production injects nothing and uses [`src/features/feedback/services/feedbackService.ts`](./src/features/feedback/services/feedbackService.ts) (`POST /api/feedback`, comment trimmed + capped at 2 KB). Tests inject a jest mock — no module patching. The standalone demo in `App.tsx` also injects a fake submit so the SUBMITTING → THANK_YOU transition works without a backend.

**Accessibility** — portal-mounted modal with focus trap + restoration, ESC, backdrop dismiss, `aria-modal` + labelled title; thank-you toast is `role="status" aria-live="polite"`; the NEGATIVE form has a `role="status"` live region that announces "Submitting your feedback…" while pending; `prefers-reduced-motion` honoured.

**Run** — `npm install && npm start` opens the demo (click the button). `npm test` runs all 28 tests (FSM transitions, service contract, integration flow incl. ESC / failure / STELLAR → Trustpilot).

## Repository layout

```
src/
├── App.tsx, App.css      ← minimal launcher
├── index.tsx
├── setupTests.ts
└── features/feedback/    ← the assignment (see its own README for details)
    ├── FeedbackWidget.tsx + test
    ├── components/       (Modal, RatingPrompt, NegativeFeedbackForm,
    │                      ThankYouToast, TrustpilotPrompt + CSS)
    ├── hooks/            (useFocusTrap, useEscapeKey,
    │                      useAutoDismiss, useStableId)
    ├── services/         (feedbackService + test)
    ├── state/            (feedbackMachine + test)
    └── index.ts
```
