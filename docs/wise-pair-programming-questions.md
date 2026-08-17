# Wise (ex-TransferWise) — Pair Programming Interview: every question I could find

Compiled from Wise's own careers pages, Glassdoor/Blind/LeetCode/Taro/enginEBogie candidate
reports, and third-party interview guides. Last researched: **August 2026**.

Each item is tagged with how much to trust it:

- **[OFFICIAL]** — stated by Wise on `wise.jobs`.
- **[REPORTED]** — a named candidate report (Glassdoor / Blind / LeetCode / Taro / enginEBogie / Medium),
  with role and location where known.
- **[AGGREGATOR]** — from SEO interview-guide sites (Dataford, Ophyai, JobRise, SystemDesignHandbook).
  These recycle and sometimes *synthesise* content. Treat as a plausible theme, not as evidence
  that the exact question was asked.
- **[PATTERN]** — not reported anywhere; my inference of what fits Wise's stated criteria.
  Prep value, zero evidence.

---

## 1. The format (so you know what the questions have to fit into)

There are **two different pair-programming pages** on Wise's careers site — backend and front end —
and they describe noticeably different rounds.

### Front End Pair Programming [OFFICIAL]

- ~**60 minutes**: 5–10 min intros, **~40 min coding together**, rest for your questions.
- Platform: "an interactive coding platform like **HackerRank**" (some reports say CoderPad).
- Language: **JavaScript**. They want "familiarity with the language and **modern patterns**".
- Front-end technical interviewing = **2 rounds**: this one + **Front End System Design**.
- Assessed on:
  - **Collaboration & communication** — work *with* the interviewer, rationalise choices out loud.
  - **Architecture** — why you made a decision, and the trade-offs you saw.
  - **Problem solving** — approach a small problem in a fixed time.
  - **Code quality** — clarity, **testability**, readability.
- **AI tools are explicitly not allowed** during this stage.

### Backend Pair Programming [OFFICIAL]

- Same 60-minute shape, HackerRank, your language of choice.
- Stated scope, which is broader than the front-end page:
  - algorithms, data structures, **space/time complexity**, **concurrency**;
  - language-specific features — implementing data structures, concurrency primitives,
    exceptions, **memory management**;
  - **industry practices — SOLID, DRY, KISS**.
- Official example prompts: *"sorting 2 lists of intervals"*, or *"designing and optimising code
  for a system you can be expected to work with at Wise"*.
- After you solve it: discussion of **how it could be better** and **what you'd change to support
  additional requirements**.
- "We're mostly interested to learn how you think, so please provide a narrative as you go."

### Front End System Design (the round right after) [OFFICIAL]

- ~60 min, ~45 min designing/diagramming a feature or product from a set of product requirements,
  "mirroring a real world example". draw.io / HackerRank diagramming, screen shared.
- Topics: **application performance, good UX, good API design**.
- Explicitly **not** about trick questions, Big O, or any particular CS concept.

---

## 2. Actual reported pair-programming questions

These are the concrete ones. Note how few are LeetCode-shaped — the pattern is
**"build a small production-ish component, then extend it"**.

| # | Question | Source / role |
|---|---|---|
| 1 | **Implement a circuit breaker with response caching for an existing request handler.** Specifically: implement the circuit breaker inside `WebClient.execute(Request)`, with rules for downstream failure handling. Clarifying discussion centred on **how `HALF_OPEN` should behave**. Round run by two Head-of-Engineering-level interviewers. | [REPORTED] Glassdoor question page + Taro (Senior SWE, London, 2025) + Medium write-up (Senior BE, London) |
| 2 | **Implement a circuit breaker** — "no tricks, no complex logic". Follow-ups: how it deploys when the app runs across **multiple pods**, and why you'd avoid a central store (doubles network IO, becomes a SPOF). | [REPORTED] LeetCode, Staff SWE, Hyderabad, 2026 (offer) |
| 3 | **Implement a thread-safe Token Manager** — token expiry, renewal, concurrent access. Candidate noted explicitly it was **"not an algorithm test"**. Two developers in the room. | [REPORTED] Glassdoor, London, May 2026 |
| 4 | **Implement a rate limiter.** (Also appears as a system-design variant: a scalable, distributed rate limiter protecting public APIs at low latency.) | [REPORTED] Senior SWE report; [AGGREGATOR] for the distributed framing |
| 5 | **N × N Tic-Tac-Toe: implement the win-condition logic.** | [REPORTED] enginEBogie, Senior SWE |
| 6 | **Sorting / merging two lists of intervals.** | [OFFICIAL] example on the backend PP page |
| 7 | **"Design and optimise code for a system you can be expected to work with at Wise."** (Deliberately open-ended — the interviewer picks a Wise-flavoured domain.) | [OFFICIAL] |
| 8 | **A money-transfer question — model it and implement it.** Described by one candidate as an easy question about a money transfer system in the live-coding round. | [REPORTED] via JobRise summary of candidate reports |
| 9 | **Refactor a currency-conversion / money-transfer pipeline** to handle validation edge cases and routing correctly. | [AGGREGATOR] |
| 10 | **"A simple OOP problem"** — no further detail; candidate passed by coding neatly and narrating. Multiple independent reports describe the round as OOP-flavoured rather than algorithmic. | [REPORTED] Blind, multiple |
| 11 | **An easy/medium HackerRank problem, then follow-ups and variations** to see how you adapt the solution. This is the single most common description of the round. | [REPORTED] Blind, multiple |
| 12 | **"Basically a Java code-fluency exercise"** in CoderPad — write clean Java, not algorithms. | [REPORTED] Blind |
| 13 | **Android-specific pair programming** for mobile roles (HackerRank, told in advance it would be Android-specific). | [REPORTED] Blind, Android dev, London |

### Adjacent rounds that get confused with pair programming

- **Prescreen / OA**: one **DSA-style** question + one **REST-API-style** problem. [REPORTED]
- **HackerRank OA**: 90 min, MCQs (OOP + general engineering) **plus** coding — algorithms **and SQL**,
  all mid-level. Another report: a 2-question OA. Another: 3 coding problems + ~20 questions in 90 min. [REPORTED]
- **Take-home / practical exercise** themes: build a **currency-converter API** (source currency,
  target currency, amount → converted amount, with **rate caching** so rates are fresh but not
  refetched per request); process **transfer status events that arrive out of order or duplicated**.
  Wise states it values **quality over completeness** — 80% well-tested and documented beats 100%
  untested, and they want a README section on *"what I'd do with more time"*. [AGGREGATOR + OFFICIAL guidance]
- **Backend system design** reported: high-concurrency stock-selling/crowdfunding event without
  degrading the core **Balance Service**. [REPORTED enginEBogie]

---

## 3. The follow-up questions (this is where the round is actually won)

Wise's own page says the exercise is followed by *"how could this be better"* and *"what would you
change to support additional requirements"*. Reported and official follow-ups:

- How would you **test** this? Write a test for it now.
- What's the **time/space complexity**? [OFFICIAL scope, backend]
- Now make it **thread-safe / concurrent-safe**. What breaks under concurrent access? [REPORTED — Token Manager, circuit breaker]
- What happens when this runs in **multiple pods/instances**? Do you need shared state?
  Why is a central store a bad idea here? [REPORTED — Staff SWE]
- How should the **HALF_OPEN** state behave? How many trial requests? What resets it? [REPORTED]
- What if the input is **10× / 1000× bigger**?
- What would you change to add `<new requirement>`? (They deliberately mutate the problem.)
- Where does this violate **SOLID / DRY / KISS**? [OFFICIAL scope]
- What are the **failure modes**, and what does the user see when it fails?
  (Wise cares a lot about clarity in **financial UX** and error handling.)
- What did you trade off, and what would you do differently with more time?

---

## 4. Front-end specific prep bank

No first-hand front-end pair-programming prompt is published anywhere I could find — every concrete
report is backend/mobile. So this section is **[PATTERN]**: exercises that match what the official
front-end page says it tests (JavaScript, modern patterns, correctness, readability, **testability**,
architecture trade-offs, small problem in 40 minutes).

**Highest-probability JS utilities** (the standard 40-minute-with-follow-ups shape):

- `debounce` / `throttle` — then add `leading`/`trailing`, then `.cancel()`, then `this` binding.
- **EventEmitter** — `on` / `off` / `emit` / `once`, then handler removal during emit.
- **Promise utilities** — `Promise.all`, `.allSettled`, `.any`, a promise pool / concurrency limiter,
  retry with backoff. (Very on-brand for a payments company talking to flaky downstreams.)
- **Polyfills** — `Array.prototype.map/filter/reduce`, `Function.prototype.bind`, `deepClone`, `groupBy`.
- **A cache with TTL** — the front-end mirror of the Token Manager / circuit-breaker questions:
  memoise an async fetch, dedupe in-flight requests, expire entries. If any backend question ports
  cleanly to the front-end round, it's this one.
- **Client-side rate limiting / request queue** — same reason.
- **Data transformation** — flatten a nested transaction tree, aggregate balances by currency,
  build a lookup index. Wise-flavoured input data is likely.

**If it's React/DOM rather than pure JS** (their stack is React + TypeScript):

- **Typeahead / autocomplete** with debounced search, race-condition handling on out-of-order
  responses, and keyboard navigation.
- **Currency amount input** — formatting, locale, floating-point money handling, validation.
- **Paginated / infinite transaction list** with loading and error states.
- A **stateful widget built incrementally**, feature by feature, as the interviewer adds requirements —
  which is structurally the same exercise as the bunq feedback widget in this repo.

**Front End System Design prompts to expect** [PATTERN, from the official topic list]:
design a currency converter / send-money flow; a transactions dashboard with filtering;
a notification system; a multi-step KYC form — assessed on performance, UX, and API design.

---

## 5. Rules and tips worth internalising

- **No AI assistance** during the pair-programming round. Stated explicitly. [OFFICIAL]
- **Pick your own language**; for the front-end round that's JavaScript. [OFFICIAL]
- **Narrate constantly.** Every source, official and candidate, converges on this. The problem is
  usually easy; silent correctness fails this round.
- **Treat interviewers as colleagues, not examiners.** Ask clarifying questions before coding —
  the successful circuit-breaker reports all start with clarifying `HALF_OPEN` semantics.
- **Expect two interviewers.** One negative London report describes them as interruptive and
  detail-focused, with one asking questions while the other said "let's move on" — be ready to
  manage two conflicting streams calmly.
- **Write tests, or at least say how you'd test it.** Testability is a named criterion on the
  front-end page.
- Wise weights **product thinking, ownership and customer impact** heavily — they're moving real
  money, and a race condition duplicates a transfer. Connect your technical choices to that.
- Don't over-index on LeetCode. Index on: clean small OO/functional design, concurrency and state
  machines, caching and resilience patterns, and extending your own code under changing requirements.

---

## 6. Sources

Official:
- [Front End Pair Programming — Wise.jobs](https://wise.jobs/front-end-pair-programming)
- [Backend Pair Programming Interviews — Wise.jobs](https://wise.jobs/backend-pair-programming-interviews)
- [Pair Programming Interviews — Wise.jobs](https://wise.jobs/pair-programming-interviews)
- [Front End System Design — Wise.jobs](https://wise.jobs/front-end-system-design)
- [Engineering Interviews — Wise.jobs](https://wise.jobs/engineering-interviews)
- [Interviews (step 2) — Wise.jobs](https://wise.jobs/step-2-interview)
- [Pair Programming interviews at Wise (2020 blog)](https://www.wise.jobs/2020/10/13/pair-programming-interviews-at-transferwise/)
- [Application and Interview Tips for Mobile Engineers](https://wise.com/gb/blog/application-and-interview-tips-for-mobile-engineers-transferwise)

Candidate reports:
- [Glassdoor — "Implement a circuit breaker with response caching for an existing request handler"](https://www.glassdoor.co.in/Interview/Implement-a-circuit-breaker-with-response-caching-for-an-existing-request-handler-QTN_8474097.htm)
- [Glassdoor — Wise Senior Software Developer](https://www.glassdoor.com/Interview/Wise-Senior-Software-Developer-Interview-Questions-EI_IE637715.0,4_KO5,30.htm)
- [Glassdoor — Wise Frontend Engineer](https://www.glassdoor.com/Interview/Wise-Frontend-Engineer-Interview-Questions-EI_IE637715.0,4_KO5,22.htm)
- [LeetCode — Staff SWE, Hyderabad 2026, offer](https://leetcode.com/discuss/post/8346937/)
- [LeetCode — Wise (formerly TransferWise), ghosted](https://leetcode.com/discuss/post/7364263/wise-formerly-transferwise-interview-exp-zkny/)
- [enginEBogie — Wise Senior Software Engineer](https://enginebogie.com/interview/experience/wise-senior-software-engineer/1206)
- [Taro — Senior SWE, London, Aug 2025](https://www.jointaro.com/interviews/companies/wise/experiences/senior-software-engineer-london-england-august-1-2025-no-offer-negative-68e9f663/)
- [Taro — SWE, London, Dec 2024, accepted](https://www.jointaro.com/interviews/companies/wise/experiences/software-engineer-london-england-december-23-2024-accepted-offer-positive-fa0bfe17/)
- [Medium — Wise London Interview Experience (Smriti Shaw)](https://debugging-tale.medium.com/wise-london-interview-experience-e63901135c59)
- Blind: [1st pair programming interview](https://www.teamblind.com/post/WiseTransferwise-1st-pair-programming-interview-Z31LdDtC) ·
  [what should I expect](https://www.teamblind.com/post/wise-transferwise-pair-programming-interview-what-should-i-expect-r7cez0s2) ·
  [pair programming round at Wise London](https://www.teamblind.com/post/pair-programming-round-at-wise-london-ujxjdsi6) ·
  [pair programming interview (ex-TransferWise) London](https://www.teamblind.com/post/pair-programming-interview-with-wise-ex-transferwise-london-vfokykqo) ·
  [Android developer interview](https://www.teamblind.com/post/android-developer-interview-with-wise-london-b3na1zz3)

Aggregators (lower confidence):
- [Dataford — Wise SWE guide](https://dataford.io/interview-guides/wise/software-engineer)
- [Ophyai — Wise interview process 2026](https://ophyai.com/blog/company-guides/wise-interview-guide)
- [JobRise — Wise engineering interview 2026](https://jobrise.io/en/blog/wise-software-engineer-interview/)
- [Prepfully — Wise SWE questions](https://prepfully.com/interview-questions/wise/software-engineer)
- [InterviewQuery — TransferWise SWE guide](https://www.interviewquery.com/interview-guides/transferwise-software-engineer)
