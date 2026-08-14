# Feedback Widget

Feature-rating popup for the bunq frontend assignment.

**Flow** — a finite-state machine in [`src/features/feedback/state/feedbackMachine.ts`](./src/features/feedback/state/feedbackMachine.ts):
`CLOSED → RATING → { NEGATIVE_FORM → SUBMITTING → THANK_YOU | THANK_YOU } → { CLOSED | TRUSTPILOT }`.
`Action` is a discriminated union; the reducer's `default` holds `const _: never = action`, so a new variant without a case is a compile error.

**DI seam** — the widget accepts a `submitFeedback` prop (typed `(p: FeedbackPayload) => Promise<unknown>`). Production injects nothing and uses [`src/features/feedback/services/feedbackService.ts`](./src/features/feedback/services/feedbackService.ts) (`POST /api/feedback`, comment trimmed + capped at 2 KB). Tests inject a jest mock — no module patching. The standalone demo in `App.tsx` also injects a fake submit so the SUBMITTING → THANK_YOU transition works without a backend.

**Accessibility** — portal-mounted modal with focus trap + restoration, ESC, backdrop dismiss, `aria-modal` + labelled title; thank-you toast is `role="status" aria-live="polite"`; the NEGATIVE form has a `role="status"` live region that announces "Submitting your feedback…" while pending; `prefers-reduced-motion` honoured.

**Run** — `npm install && npm start` opens the demo (click the button). `npm test` runs all 28 tests (FSM transitions, service contract, integration flow incl. ESC / failure / STELLAR → Trustpilot).

## System design notes

[`docs/system-design/cross-border-payments.md`](./docs/system-design/cross-border-payments.md) —
multi-currency cross-border payment service (GBP → EUR into a German bank via SEPA): 24-hour rate
reservation and TTL expiry, the transfer state machine, a double-entry ledger on PostgreSQL,
synchronous vs. asynchronous compliance, and what happens to quotes that are never funded.

[`docs/system-design/cross-border-payments-explained.md`](./docs/system-design/cross-border-payments-explained.md) —
companion walkthrough of the same design from zero: what actually happens to the money, debits and
credits for engineers, a section-by-section explanation, a jargon dictionary, and how to deliver it
out loud.

[`docs/system-design/cross-border-payments-staff.md`](./docs/system-design/cross-border-payments-staff.md) —
the same system end to end at staff level: the full hop-by-hop trace of one payment, the invariant
set with how each is enforced and detected in production, failure domains and the degradation
ladder, how the design survives three years of change, testing and assurance, capacity/cost/DR, and
ownership.

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
