# Feedback Widget

Feature-rating popup for the bunq frontend assignment.

**Flow** — a finite-state machine in [`src/features/feedback/state/feedbackMachine.ts`](./src/features/feedback/state/feedbackMachine.ts):
`CLOSED → RATING → { NEGATIVE_FORM → SUBMITTING → THANK_YOU | THANK_YOU } → { CLOSED | TRUSTPILOT }`.
`Action` is a discriminated union; the reducer's `default` holds `const _: never = action`, so a new variant without a case is a compile error.

**DI seam** — the widget accepts a `submitFeedback` prop (typed `(p: FeedbackPayload) => Promise<unknown>`). Production injects nothing and uses [`src/features/feedback/services/feedbackService.ts`](./src/features/feedback/services/feedbackService.ts) (`POST /api/feedback`, comment trimmed + capped at 2 KB). Tests inject a jest mock — no module patching. The standalone demo in `App.tsx` also injects a fake submit so the SUBMITTING → THANK_YOU transition works without a backend.

**Accessibility** — portal-mounted modal with focus trap + restoration, ESC, backdrop dismiss, `aria-modal` + labelled title; thank-you toast is `role="status" aria-live="polite"`; the NEGATIVE form has a `role="status"` live region that announces "Submitting your feedback…" while pending; `prefers-reduced-motion` honoured.

**Run** — `npm install && npm start` opens the demo (click the button). `npm test` runs all 164 tests (FSM transitions, service contract, integration flow incl. ESC / failure / STELLAR → Trustpilot, plus the experimentation framework below).

## Experimentation

[`src/features/experiments/`](./src/features/experiments) is a dependency-free A/B testing framework — deterministic bucketing (FNV-1a + murmur3 avalanche, salted per experiment), React bindings whose exposure logging fires only when the user can actually see the treatment, and the statistics to size and read a test (sample size, two-proportion z-test, SRM, Benjamini–Hochberg, CUPED). [`examples/feedbackExperiments.tsx`](./src/features/experiments/examples/feedbackExperiments.tsx) runs a real experiment on this widget's thank-you toast.

Two accompanying guides, each running beginner → staff with worked numbers, interview drills and a cheat sheet:

- [**docs/ab-testing.md**](./docs/ab-testing.md) — the general track: hypothesis and metric design, sizing, frontend implementation (bucketing, dilution, flicker, QA overrides), trustworthiness (SRM, peeking, interference, novelty), platform and organisational design.
- [**docs/ab-testing-ml.md**](./docs/ab-testing-ml.md) — the ML engineering track: why offline metrics don't ship, sizing for 1% effects, triggered analysis, the serving-side logging contract (propensities!), clustered ratio metrics, feedback loops through retraining, interleaving, off-policy evaluation, MLRATE, bandits, LLM feature evaluation, and model governance in a regulated shop.

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
└── features/experiments/ ← A/B testing framework (see docs/ab-testing.md)
    ├── core/             (hash, assign, overrides + tests)
    ├── analysis/         (stats, cuped/MLRATE, ratioMetrics, offPolicy,
    │                      interleaving, sequential + tests)
    ├── examples/         (feedbackExperiments + test)
    ├── ExperimentProvider.tsx + test
    ├── types.ts
    └── index.ts
```
