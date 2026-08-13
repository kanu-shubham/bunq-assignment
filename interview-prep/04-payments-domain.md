# 04 — The payments domain

Most candidates for this role will be competent backend engineers who have never thought
hard about money. This section is your differentiator, and it's cheap to acquire.

---

## 1. Money is not a number

**Never `float`/`double`.** 0.1 has no exact binary representation, so `0.1 + 0.2 != 0.3` —
in Java, in JavaScript, everywhere. `MoneyTest` asserts this. Fractions of a penny compound
into unexplained ledger drift, and "unexplained" is the word that ends careers in this domain.

**Store integer minor units.** £12.34 is `1234`. Money is counted, not measured.

**Scale is per currency.** GBP and EUR have 2 decimal places, **JPY has 0**, and KWD/BHD/JOD
have **3**. Hard-coding `* 100` breaks in Tokyo and Kuwait. `Money.parse` reads
`Currency.getDefaultFractionDigits()`.

**Currency is part of the value.** Adding GBP to EUR is a bug, not a conversion. `Money.plus`
throws on a mismatch.

**Reject excess precision.** `12.345` GBP is a malformed request, not a rounding opportunity.
Silently rounding it is how you get a penny of drift per thousand payments.

**Don't put amounts in JSON numbers.** Most parsers make them doubles, so `10.10` can arrive
as `10.099999999999999`. Take a digit string and parse it yourself. Stripe, Adyen and
GoCardless all do this, for this reason.

---

## 2. Double-entry bookkeeping

The 700-year-old idea that underpins every ledger.

**Every transaction is at least two entries, and they sum to zero.** Money is never created or
destroyed; it moves between accounts.

Sending £100 from a customer to a beneficiary via your float:

| Account | Debit | Credit |
|---|---|---|
| Customer GBP wallet | 100.00 | |
| GBP float / settlement | | 100.00 |

**Why it matters:** the invariant `sum(all entries) == 0` is checkable. Run it continuously
and any bug that creates or destroys money is caught in minutes rather than at audit.

**Consequences to be able to state:**

- **Entries are immutable.** You never update or delete a ledger row. A mistake is corrected by
  a **reversing entry** — the error and its correction both stay visible forever. That's the
  audit trail, and regulators require it.
- **Balance is derived**, not stored: the sum of entries. Cached for performance, but the
  entries are the truth.
- **The ledger is the CP corner of CAP.** Refusing a payment is annoying; double-spending is
  not recoverable.

If asked to design a ledger, saying "append-only, double-entry, balance derived from entries,
corrections by reversal" in the first thirty seconds tells them you've been near real money.

---

## 3. The lifecycle, and why it's not a straight line

The `TransferStatus` state machine:

```
RECEIVED → VALIDATED → FUNDED → SUBMITTED → SETTLED
    │          │          │         │          │
    └──────────┴──────────┴─────────┴─▶ FAILED │
                                    └──▶ RETURNED ◀┘
```

Two details that show domain understanding:

**`SETTLED` is not terminal.** A beneficiary bank can return funds days later — wrong account,
closed account, a sanctions hit at their end. A model where "settled" ends the story cannot
represent that, and you find out in production.

**`SUBMITTED` is the dangerous state.** Once an instruction is on the wire, a timeout tells you
nothing. Everything past `SUBMITTED` must be **reconciled against the partner's record**, never
inferred from your request's outcome. This is the concrete, domain-specific version of §2's
"a timeout is an ambiguity".

---

## 4. Reconciliation

**The control that catches everything the code missed.** Interviewers for a payments role
notice when a candidate raises it unprompted, because it's what you build once you've been
burned.

You compare three views that should agree and usually don't:

1. Your ledger.
2. The partner bank's statement/report.
3. The scheme's record (SEPA, Faster Payments, SWIFT).

**Break types and what they mean:**

| Break | Meaning | Typical cause |
|---|---|---|
| In ours, not theirs | We think we sent it; they never got it | Lost submission, timeout that really did fail |
| In theirs, not ours | They processed something we don't know about | **The dangerous one** — a timeout we recorded as failed but which actually succeeded |
| Amount mismatch | FX or fee handling differs | Rounding, unexpected intermediary fees |
| Timing | Same payment, different day | Cut-off times, timezones, value dating |

Row 2 is the one that justifies the whole exercise: it's how you discover a payment you
believe failed but which actually moved money. No amount of application-level care finds that
— only comparing against the counterparty's record does.

**Design points:** reconcile continuously rather than nightly; every break is a ticket with an
owner, never a log line; auto-resolve the known-benign classes and alert on the rest.

---

## 5. Partner integration — the actual job

"Send for Partners" means banks and enterprises calling *your* API. That inverts your usual
assumptions.

**You are the dependency now.** Your API contract is someone else's production system. Breaking
changes require versioning and a migration window measured in quarters, not a Slack message.

**Partners retry badly.** Assume the worst client you can imagine: no jitter, no cap, retries on
400s. Your idempotency and rate limiting must survive it, because you cannot fix their code.

**Every partner is different.** Different schemes, cut-off times, file formats (ISO 20022,
proprietary CSV, SWIFT MT), reachability, working days. The architectural question this poses —
*"how do you onboard partner N+1 without a rewrite?"* — is very likely to come up. See §5 of
the design walkthrough for the answer: a stable internal domain model plus a per-partner
adapter.

**Cut-off times and value dates are real.** Payment schemes close. A payment submitted at 16:01
may settle tomorrow, and "tomorrow" depends on the destination's public holidays. This is
business logic, not an edge case, and it's the sort of thing that surprises engineers new to
the domain.

**Sandbox and certification.** Partners test against your sandbox before going live. That
environment is a first-class product with its own uptime expectations, not a staging box.

---

## 6. Compliance — enough to be credible

You are not expected to be a compliance officer. You are expected to know these shape the
architecture rather than being bolted on.

- **Sanctions screening.** Every payment screened against watchlists before it leaves.
  Synchronous in the flow, and a hit means *hold*, not fail. False positives need human review,
  so your state machine needs somewhere to park a payment pending a person.
- **AML.** Pattern monitoring — structuring, unusual corridors, velocity. Mostly asynchronous
  off the event stream, which is another good reason for the outbox.
- **KYC/KYB.** Identity verification. For Send for Partners, often *their* obligation for
  *their* customers, which raises the interesting question of who holds what data.
- **PSD2/SCA** (Europe): strong customer authentication, and open banking APIs.
- **Data residency and GDPR.** Where personal data may physically live. This constrains your
  deployment topology — a genuinely architectural constraint, and a good thing to raise.
- **Audit trail.** Immutable, complete, reconstructible. Which is the append-only ledger again.
- **Safeguarding.** E-money firms must hold customer funds separately from operating funds.
  Wise is FCA-authorised under the Electronic Money Regulations 2011 (per the job posting), so
  this is a live constraint on how accounts are modelled.

**The line to have ready:** *"In payments, compliance isn't a gate at the end — it's a
functional requirement with latency and availability budgets like anything else. Sanctions
screening is in the synchronous path, so its p99 is part of my API's p99."*

That reframing — compliance as an engineering constraint with an SLA — is what "comfort with
ambiguity while maintaining quality and compliance" in the job posting is pointing at.

---

## 7. Vocabulary

Enough to follow the conversation:

| Term | Meaning |
|---|---|
| **PSP** | Payment Service Provider |
| **Scheme / rail** | The network: SEPA, Faster Payments, SWIFT, ACH |
| **SEPA SCT / SCT Inst** | Euro credit transfer; Inst is ~10 seconds |
| **Faster Payments (FPS)** | UK near-instant, with per-transaction limits |
| **SWIFT** | Cross-border messaging (not settlement); correspondent banking |
| **ISO 20022** | The XML standard schemes are migrating to; `pain.001` initiates, `pacs.008` moves |
| **IBAN / BIC** | International account number / bank identifier |
| **Nostro / Vostro** | Our account with them / their account with us |
| **Float / liquidity** | Pre-funded balances that let you pay out before funds arrive |
| **Correspondent banking** | Chained intermediary banks for cross-border; where fees and delays come from |
| **Settlement vs clearing** | Clearing = exchanging instructions; settlement = money actually moving |
| **Value date** | The date funds are actually available |
| **Chargeback / return** | Money coming back after the fact |
| **Prefunding** | Partner deposits in advance; removes credit risk |

**Wise's actual differentiator**, worth understanding: rather than pushing money across borders
through correspondent chains, Wise holds local float in many countries and does a local payout
on each side, netting the difference. That's why it's cheap and fast — and it's why "Send for
Partners" is a compelling product for a bank that doesn't want to build it.

---

## Quick self-test

1. Why never `double` for money? → No exact binary representation of 0.1
2. £12.34 stored how? → `1234` minor units + currency
3. What does double-entry guarantee? → Entries sum to zero; money is conserved and checkable
4. Correcting a ledger mistake? → Reversing entry; never update or delete
5. Is `SETTLED` terminal? → No — returns happen days later
6. Most dangerous reconciliation break? → In theirs, not ours: a "failed" payment that moved money
7. Why is sanctions screening an architecture concern? → It's synchronous, so its p99 is in your p99
