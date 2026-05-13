# Feedback Widget

Feature-rating popup for the bunq frontend assignment.

**Flow** — a finite-state machine in [`state/feedbackMachine.ts`](./state/feedbackMachine.ts):
`CLOSED → RATING → { NEGATIVE_FORM → SUBMITTING → THANK_YOU | THANK_YOU } → { CLOSED | TRUSTPILOT }`.
`Action` is a discriminated union; the reducer's `default` holds `const _: never = action`, so a new variant without a case is a compile error.

**DI seam** — the widget accepts a `submitFeedback` prop (typed `(p: FeedbackPayload) => Promise<unknown>`). Production injects nothing and uses [`services/feedbackService.ts`](./services/feedbackService.ts) (`POST /api/feedback`, comment trimmed + capped at 2 KB). Tests inject a jest mock — no module patching.

**Accessibility** — portal-mounted modal with focus trap + restoration, ESC, backdrop dismiss, `aria-modal` + labelled title; thank-you toast is `role="status" aria-live="polite"`; the NEGATIVE form has a `role="status"` live region that announces "Submitting your feedback…" while pending; `prefers-reduced-motion` honoured.

**Run** — `npm install && npm start` opens the demo (click the button). `npm test` runs all 28 tests (FSM transitions, service contract, integration flow incl. ESC / failure / STELLAR → Trustpilot).
