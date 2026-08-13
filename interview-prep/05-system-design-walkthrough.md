# 05 — System design walkthrough

> **"Design the system that lets a partner bank offer Wise transfers to their customers
> through their own app."**

This is the round most likely to decide the outcome, and it is very likely to be some version
of the above, because it's the team's actual job. Below is a full worked answer with a timing
plan.

Practise it **out loud, to a timer, three times**. Reading it is worth a fraction of saying it.

---

## Timing (45 minutes)

| Minutes | Phase | The mistake to avoid |
|---|---|---|
| 0–5 | Clarify requirements | Diving into boxes before knowing what you're building |
| 5–10 | Scope, scale, SLOs | Vague "it should be fast" |
| 10–20 | High-level architecture | Too much detail too early |
| 20–35 | Deep dive on 2–3 areas | Letting them pick all of them — steer to your strengths |
| 35–42 | Failure modes | **Where most candidates run out of time — protect this** |
| 42–45 | Trade-offs, what you'd do next | Pretending the design is finished |

**Say the plan out loud at the start.** *"I'll spend five minutes on requirements, sketch the
architecture, then go deep on idempotency and partner failure handling — stop me if you'd
rather go elsewhere."* It signals structure and lets them redirect you early.

---

## Phase 1 — Clarify (0–5 min)

Ask these. Don't ask all of them; pick the four that most change the design.

**Functional**
- Payment initiation only, or also quotes, status, cancellation, refunds?
- Who holds the customer relationship and the KYC obligation — us or the partner?
- Does the partner prefund, or do we extend credit?
- Single payments only, or batch files too? (Banks love batch files.)

**Non-functional**
- How many partners — 5 or 500? *(This is the highest-leverage question in the whole round;
  it determines whether you build an adapter framework or hard-code two integrations.)*
- Volume and shape? Steady, or payroll spikes on the 1st and 15th?
- Latency expectation for accept? For settlement?
- Which corridors and currencies at launch?

**Assumptions to state if they say "you decide"** — always state them explicitly:

> 50 partners at launch, 500 within two years. 10M payments/day peak (~120/s average, 1000/s
> peak on payroll days). Accept in under 300ms p99. Settlement varies by rail: seconds for
> SEPA Inst, up to two days for some corridors. Partners prefund. We're the PSP; the partner
> owns their customer's KYC.

---

## Phase 2 — Scope and SLOs (5–10 min)

Write the SLOs down; most candidates never do, and it's a differentiator.

| Concern | Target | Note |
|---|---|---|
| Accept API availability | 99.99% | ~4 min/month |
| Accept latency | p99 < 300ms | Sanctions screening is inside this budget |
| Settlement (SEPA Inst) | p95 < 30s | Rail-dependent, not ours to control |
| Duplicate payments | **Zero** | Not a percentage. The one hard invariant. |
| Ledger integrity | Entries sum to zero, continuously verified | |

Then say the thing that frames everything after it:

> *"The hard invariant is exactly-once **effect**: a partner can retry any request as many
> times as they like and the money moves once. Everything else is a trade-off; that one isn't."*

---

## Phase 3 — High-level architecture (10–20 min)

```
   Partner Bank
        │  POST /v1/transfers  (Idempotency-Key, mTLS)
        ▼
 ┌─────────────────┐
 │  Edge / Gateway │  authn, per-partner rate limit, schema validation
 └────────┬────────┘
          ▼
 ┌─────────────────┐     ┌──────────────┐
 │ Transfer API    │────▶│ Idempotency  │  claim/replay, PK-enforced
 │ (stateless)     │     │ store        │
 └────────┬────────┘     └──────────────┘
          │ one transaction
          ▼
 ┌─────────────────────────────────┐
 │ Transfer DB   +   Outbox table  │   ← atomic
 └────────┬────────────────────────┘
          │ poller / CDC
          ▼
 ┌─────────────────┐
 │  Event bus      │  partitioned by transferId
 └───┬────┬────┬───┘
     │    │    │
     ▼    ▼    ▼
 ┌──────┐┌──────────┐┌────────────┐
 │Ledger││Compliance││ Orchestrator│──▶ Partner adapters ──▶ SEPA / FPS / SWIFT
 └──────┘└──────────┘└────────────┘         (per rail)
                            │
                            ▼
                     ┌──────────────┐
                     │Reconciliation│ ◀── partner statements
                     └──────────────┘
```

**Narrate the request path**, don't just draw it:

1. Partner POSTs with an `Idempotency-Key` over mTLS.
2. Gateway authenticates, applies that partner's rate limit, validates the schema.
3. Transfer API claims the idempotency key. Replay → return the stored response, done.
4. **One transaction**: write the transfer (`RECEIVED`) and an outbox event.
5. Return **`202 Accepted`** with a transfer id and status. *We do not call the partner bank
   synchronously* — more on this below.
6. Outbox poller publishes to the bus, partitioned by transfer id.
7. Ledger reserves funds; compliance screens; orchestrator drives the saga.
8. The rail adapter submits, and its acknowledgement moves the transfer to `SUBMITTED`.
9. Webhooks and reconciliation resolve it to `SETTLED` or `RETURNED`.
10. We notify the partner by webhook; they can also poll `GET /transfers/{id}`.

### Defend the `202`

They will push on this, so lead with it:

> *"The accept path does no network I/O beyond its own database. Calling the partner bank
> inside the request would mean holding a DB transaction across a network call — a partner
> taking 30 seconds exhausts the connection pool long before it recovers, and takes down
> every unrelated endpoint. Worse, if the partner call succeeded and our commit then failed,
> we'd have moved money with no local record of it. So we persist, return 202, and settle
> asynchronously. The status endpoint and webhooks tell them the rest."*

That single answer demonstrates §2 (dual writes), §3 (bulkheading and pool exhaustion) and
honest API design at once.

---

## Phase 4 — Deep dives (20–35 min)

Steer toward these. Have all four ready; you'll get through two or three.

### 4a. Idempotency (your strongest — lead with it)

Walk the protocol from §2: claim → work → complete, with the primary key doing the arbitration
and the request hash catching key reuse. Cover the four probes: why not check-then-insert, why
hash the body, what if we crash mid-request, how long to retain.

Then add the partner-facing dimension, which is specific to *this* role:

- **We depend on the partner's key**, and partners generate keys badly. Some reuse them across
  days; some send the same key for genuinely different payments. Hence the 422 rather than a
  silent replay.
- **Defence in depth:** also enforce a unique constraint on `(partnerId, partnerReference)`.
  If a partner's key hygiene fails, their own payment reference still catches the duplicate.
- **Our key going downstream** must be stable across our retries — derived from the transfer
  id, never regenerated per attempt.

### 4b. Onboarding partner N+1 without a rewrite

If they asked "how many partners", this is where it pays off.

> *"A stable internal domain model, and a thin adapter per partner. The adapter's only job is
> translating our model to their format — ISO 20022, a proprietary CSV, whatever — and
> normalising their status codes into our state machine. Partner-specific behaviour that isn't
> protocol (cut-off times, limits, supported corridors, retry policy) is configuration, not
> code, so onboarding is mostly config plus one adapter, and the core never changes."*
>
> *"The failure mode to design against is partner-specific logic leaking into the core. Once
> there's an `if (partner == 'X')` in the orchestrator, you've got a monolith with 500
> special cases. I'd enforce that boundary in code review from the first integration, because
> it's very hard to claw back later."*

### 4c. Handling partner failure

Everything in §3, applied:

- Timeouts sized from each partner's observed p99, configured **per partner**.
- Retries only on retryable classifications, exponential backoff with jitter, small budget.
- Circuit breaker **per partner** — one partner's outage must never affect another's payments.
  This is the key sentence.
- Bulkhead per partner so a hanging partner can't drain a shared pool.
- Business rejections excluded from the breaker's window.
- When a breaker opens: payments queue in `FUNDED`, we alert, and we tell the partner honestly
  via status rather than failing their payment outright.

Then the ambiguity, which is the payments-specific part:

> *"If submission times out, we don't know whether it landed. We never assume failure — that's
> how you double-pay on retry. The transfer stays `SUBMITTED`, and it's resolved by querying
> the partner's status endpoint, or by reconciliation against their statement if they don't
> have one. The reconciliation break 'in theirs, not ours' exists precisely to catch this."*

### 4d. Ledger and consistency

- Double-entry, append-only, balance derived from entries.
- The ledger is the CP corner: single writer per account, strong consistency, refuse rather
  than risk a double-spend.
- Optimistic locking (`@Version`) for concurrent updates to one transfer.
- Continuous invariant check that entries sum to zero.
- Everything else (status reads, dashboards) can be eventually consistent off the event stream.

---

## Phase 5 — Failure modes (35–42 min)

**Protect this time.** Most candidates run out and it's the most senior-signalling section.
Volunteer it: *"Let me walk through what breaks."*

| Failure | Response |
|---|---|
| Partner bank down | Per-partner breaker opens; payments hold in `FUNDED`; others unaffected; alert |
| **Partner call times out** | **Never assume failure.** Stay `SUBMITTED`; resolve by status query or reconciliation |
| Our DB primary fails | Failover replica; accept API returns 503 + `Retry-After` meanwhile — partners retry with the same key, so it's safe |
| Event bus down | Outbox rows accumulate; nothing is lost; drains on recovery (that's the point of the outbox) |
| Poller crashes mid-batch | Rows stay unpublished; republished later; consumers dedupe on `eventId` |
| Duplicate event delivered | Consumers idempotent by `eventId` |
| Events arrive out of order | State machine rejects impossible transitions |
| Partner floods us | Per-partner rate limit at the edge; one partner cannot consume another's capacity |
| Poison event | Attempts counted, moved to dead-letter after a threshold, alerted — never retried forever |
| Bad deploy | Blue/green or canary; DB migrations backwards-compatible (expand/contract) so rollback is possible |
| **Bug credits the wrong account** | Reversing entries, not deletions. Freeze affected accounts, reconcile, publish a corrected trail |

That last row is worth saying explicitly. It shows you understand corrections in a regulated,
audited system are themselves transactions.

---

## Phase 6 — Trade-offs (42–45 min)

Never claim the design is finished. Name what you traded:

> *"Three things I'd flag. First, async settlement means partners need webhooks or polling —
> more integration work for them than a synchronous API, and I'd validate that with Solutions
> Engineering before committing. Second, the outbox poller adds up to 500ms of latency; if
> that matters I'd move to CDC with Debezium, but that's another piece of infrastructure to
> run and I wouldn't buy it before we need it. Third, per-partner circuit breakers and rate
> limits mean per-partner configuration, which is real operational surface — it needs good
> defaults and a safe way to change them without a deploy."*
>
> *"If I were starting this for real, I'd build the idempotency layer and the ledger first
> with one partner integration end to end, then extract the adapter abstraction once I'd seen
> two real partners. Extracting it from one example usually produces the wrong abstraction."*

That last sentence is a leadership answer as much as a technical one.

---

## Rehearsal checklist

Cover the page. Can you, out loud and unprompted:

- [ ] Ask four clarifying questions and state your assumptions?
- [ ] Draw the architecture in under four minutes?
- [ ] Explain why accept returns 202 and does no network I/O?
- [ ] Walk the idempotency protocol including the concurrent-duplicate case?
- [ ] Explain the outbox and why the dual write is unsafe?
- [ ] Say where the circuit breaker goes relative to the retry, and why?
- [ ] Explain what happens when partner submission times out?
- [ ] Name six failure modes and their responses?
- [ ] State three trade-offs you consciously made?

If you can do all nine out loud without notes, you're ready for this round.
