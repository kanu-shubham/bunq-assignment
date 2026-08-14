# The same design, explained from zero

Companion to [`cross-border-payments.md`](./cross-border-payments.md). That document is the *answer*.
This one explains what every part of it means and why it is there, assuming no background in
payments, FX, or accounting — so you can defend it under questioning rather than recite it.

Read order: **§A** (what actually happens to the money) → **§B** (debits and credits) → **§C**
(section-by-section walkthrough) → **§E** (how to deliver it out loud). §D is the jargon dictionary,
for looking things up as you go.

---

## A. What actually happens to the money

Forget databases for a minute. Alice in London wants her friend Ben in Berlin to receive €1,151.
She has pounds. He needs euros in his German bank account.

### A.1 The naïve mental model (wrong)

> Alice's £1,000 travels through some international pipe, gets converted somewhere in the middle,
> and comes out as euros in Ben's account.

Nothing about this is true, and believing it produces a bad design — you end up modelling a single
"transfer" object moving through foreign banks, and you cannot explain where the money *is* at any
given second.

### A.2 What really happens

We (the payment company) already have two piles of money before Alice ever shows up:

- a **GBP pile**: a bank account in the UK, holding pounds;
- a **EUR pile**: a bank account at a partner bank inside the euro area, holding euros. Having put
  money there in advance is called **pre-funding**.

Now:

1. **Alice pays us £1,000** using a normal domestic UK bank transfer (Faster Payments). Domestic.
   Never leaves the UK. Our GBP pile grows by £1,000.
2. **We write in our own books** that Alice's claim on us is no longer £995.50 (after the £4.50
   fee) but €1,151.00. This is just bookkeeping. No money moves. No bank is involved. This is the
   step everyone calls "the conversion", and it is a database write.
3. **We pay Ben €1,151** out of the EUR pile, using a normal domestic-style euro payment (SEPA).
   Also domestic-ish — it goes from our euro-area bank to Ben's German bank. Our EUR pile shrinks.

Alice's pounds never became Ben's euros. Our GBP pile got bigger and our EUR pile got smaller.

### A.3 The consequence that drives the whole design

Our two piles are now out of balance: too many pounds, not enough euros. If we do this ten thousand
times a day and never fix it, the EUR pile hits zero and payouts start bouncing.

So somebody — **treasury** — periodically goes to the currency market and swaps the accumulated
pounds for euros to refill the pile. Crucially, that happens **in bulk, later, at whatever the market
price is then** — not per customer, not at Alice's moment, and definitely not at the rate we promised
Alice.

That gap is the entire commercial risk of the product:

> We promise Alice a rate **now**. We actually buy the euros **later**. If the rate moved in
> between, the difference is our profit or our loss.

Hold that rate for 24 hours and the gap gets 24 hours wide. Everything in §3 of the design (rate
reservation, hedging, expiry) exists to manage that one sentence.

### A.4 Why it takes hours, when each step is fast

Each individual step is quick. The waiting is:

- Alice has to actually go to her banking app and send the money — that could be five minutes or
  twenty hours. **This is the 24-hour window.** The clock is human, not technical.
- Compliance checks (is Alice who she says she is, is Ben on a sanctions list).
- The SEPA payout, which is either ~10 seconds (SEPA Instant) or up to one business day (classic
  SEPA, which has cut-off times and does not run at weekends).

---

## B. Debits and credits, for engineers

The one genuinely unfamiliar bit for most developers. It is much simpler than its reputation.

### B.1 The single idea

Money never appears or disappears; it only moves. So **every entry in the books has two sides**:
where it came from, and where it went. Write both, and they must be equal. If they are not equal,
you have made an error — and the ledger can *tell* you, which is the whole point.

The vocabulary:

| Term | Plain meaning |
|---|---|
| **Debit** (Dr) | Where the money **went to** — the use |
| **Credit** (Cr) | Where the money **came from** — the source |
| **Journal / transaction** | One event, made of two or more entries that sum to zero |
| **Account** | A named bucket you track a running total for |

Ignore any half-remembered rule about "debit means increase". Use *source and destination* and you
will never get a payment journal wrong.

### B.2 Account types

| Type | Meaning | Example here |
|---|---|---|
| **Asset** | Something we own | `asset.bank.uk_fps.GBP` — pounds in our UK bank |
| **Liability** | Something we owe someone | `liability.transfer_funds.GBP:{id}` — Alice's money, held by us |
| **Income** | Money we earned | `income.fee.GBP`, `income.fx_spread.GBP` |
| **Position** | A deliberately unbalanced FX exposure | `position.fx.GBP`, `position.fx.EUR` |

The subtle one: when Alice's £1,000 lands, **we are not £1,000 richer.** Our assets went up by
£1,000 *and* our obligations went up by £1,000. We are holding her money, not owning it. That is why
customer funds are a liability, and it is also, in real firms, a regulatory requirement
(safeguarding: customer money must be identifiable and segregated, not spent on payroll).

### B.3 Reading the actual journals

**Alice's £1,000 arrives.** Money came from Alice; money went into our bank account.

| Account | Dr (went to) | Cr (came from) |
|---|---|---|
| `asset.bank.uk_fps.GBP` | 1,000.00 | |
| `liability.transfer_funds.GBP:{id}` | | 1,000.00 |

Sums to zero. Read it as a sentence: *"£1,000 went into our UK bank account; it came from Alice, so
we now owe her £1,000."*

**We take our £4.50 fee.** Money comes from what we owe Alice, and goes to our revenue.

| Account | Dr | Cr |
|---|---|---|
| `liability.transfer_funds.GBP:{id}` | 4.50 | |
| `income.fee.GBP` | | 4.50 |

We now owe Alice £995.50 and have earned £4.50. Note the bank account did not change — the pounds
are still sitting there; only the *claim on them* changed hands. Bookkeeping is about claims.

**The conversion.** Here is the trick that makes multi-currency ledgers work.

You cannot write "debit £995.50, credit €1,151.00" — those are different units, and a journal that
mixes them can never be checked for balance. £995.50 = €1,151.00 is only true at one instant, at one
rate; bake it in and your ledger stops being arithmetic and starts being an opinion.

So the rule is: **journals balance per currency, and a conversion is two separate journals joined by
a special pair of accounts.**

| Account | Dr | Cr | |
|---|---|---|---|
| `liability.transfer_funds.GBP:{id}` | 995.50 | | ← GBP leg, sums to zero |
| `position.fx.GBP` | | 995.50 | |
| `position.fx.EUR` | 1,151.00 | | ← EUR leg, sums to zero |
| `liability.transfer_funds.EUR:{id}` | | 1,151.00 | |

Sentence version: *"We stop owing Alice £995.50; the FX desk takes those pounds in. The FX desk hands
out €1,151.00; we now owe Alice that instead."*

The `position.fx.*` accounts are the only ones allowed to sit unbalanced against each other, and
that imbalance **is** the company's currency exposure — the too-many-pounds/not-enough-euros problem
from §A.3, now visible as a number you can query instead of a vibe. The desk holds pounds (long GBP)
and owes euros (short EUR).

When treasury later does the real market trade, it posts the opposite way and closes the position.
Whatever is left over is profit or loss, swept to `income.fx_spread`. **That is why a real ledger
beats a `balance` column:** "did we actually earn the spread we quoted?" becomes a query, not an
investigation.

**Paying Ben.** Two events, because dispatching and settling are different moments:

| Event | Dr | Cr | Meaning |
|---|---|---|---|
| Dispatched | `liability.transfer_funds.EUR` 1,151.00 | `liability.payouts_in_flight.EUR` 1,151.00 | we no longer owe Alice; we owe the payment system |
| Settled | `liability.payouts_in_flight.EUR` 1,151.00 | `asset.bank.partner_de.EUR` 1,151.00 | our euro pile actually shrinks |

Keeping these separate is what lets you answer "how much money have we committed but not yet paid
out?" — a number treasury needs every single day to know whether the EUR pile will run dry.

### B.4 Two rules that make it auditable

1. **Never `UPDATE` or `DELETE` a ledger entry.** Wrong entry? Post a *reversing* entry (the same
   amounts, the opposite way round), then post the correct one. History stays true. Databases
   enforce this by revoking the permissions, not by asking developers nicely.
2. **Balances are derived, never authoritative.** A `balances` table is a cache of
   `SUM(amount) GROUP BY account`. A nightly job recomputes from the entries and screams if the
   cache drifted. If you keep only a balance column, a bug that adds £5 twice is undetectable and
   permanent.

---

## C. The design document, section by section

### §1 Framing

**What it says.** Nothing crosses a border; we collect locally, rewrite our books, pay out locally.
Plus the requirements and what is deliberately out of scope.

**Why it is first.** In a design interview the framing *is* the answer to half the follow-ups. Once
the interviewer accepts "the conversion is a ledger write, and the payout comes from pre-funded
liquidity", you have pre-emptively answered: why is it fast? why is FX risk a treasury problem? why
is there no distributed transaction with a foreign bank? Candidates who skip this spend the rest of
the hour explaining accidental complexity they invented themselves.

**The numbers.** 10 M transfers/month ≈ 4 per second average, ~40/s at peak. Stating this early
kills the reflex to over-engineer: 40 writes/second is *nothing* for PostgreSQL. Saying "this is
small, so I am spending my complexity budget on correctness, not on sharding" is a senior signal.
Volunteering a Kafka-plus-sharded-NoSQL architecture for 40 tps is the opposite.

**"Correctness over availability."** A design principle that resolves ties later. When the risk
scorer is down, do we ship the payment or hold it? This sentence decided that, in advance.

### §2 Architecture

**Service boundaries follow ownership of state.** Each table has exactly one service allowed to
write it. Everyone else asks that service. This is what actually prevents a distributed monolith —
not the number of services, but the discipline about who owns which rows.

**One database, several modules.** The transfer state and the ledger live in the same PostgreSQL
cluster on purpose, so "mark it CONVERTED" and "write the conversion journal" happen in **one
transaction**: both, or neither. Split them across two services with two databases and you need a
distributed transaction or a saga, and you have invented the failure mode where a transfer says
`CONVERTED` but no money moved. At 40 tps there is no benefit to buy with that pain.

**Transactional outbox** — the pattern worth being able to draw. The problem:

```
BEGIN;  UPDATE transfers SET state='CONVERTED';  COMMIT;
        publishToKafka("converted")           ← crash here: DB says converted, nobody knows
```

Kafka is not in your database transaction, so "commit and publish" cannot be atomic. The fix: publish
into a database *table* in the same transaction, and have a separate relay process read that table
and push to the bus.

```
BEGIN;
  UPDATE transfers SET state = 'CONVERTED';
  INSERT INTO outbox (topic, payload) VALUES ('transfer.converted', …);
COMMIT;                                       ← atomic: both rows or neither
-- relay: SELECT … WHERE published_at IS NULL → publish → mark published
```

If the relay crashes after publishing but before marking, the message goes out twice. That is
accepted and named: **at-least-once delivery**. Which is why every consumer must be **idempotent** —
processing the same message twice must have the same effect as processing it once. In practice:
a unique constraint on some natural key, and a duplicate insert that harmlessly fails.

### §3 Rate reservation

**Two tiers.** A free live rate (short-lived, re-priced when the money lands) and a guaranteed
24-hour lock. Offering only the second is expensive; offering only the first is a bad product.

**Why the lock is dangerous — the option framing.** A locked rate is a free option you have written
to the customer. If the market moves in their favour they walk away and re-quote. If it moves against
them they fund and hold you to the old rate. So you do not lose "on average zero" — you lose
disproportionately on exactly the quotes that get funded. This is **adverse selection**, and it is
the sentence that shows you understand the product rather than just the endpoint. The lock price has
to cover it.

**The two things "reserve" means** (§3.3) — the distinction most candidates miss:

1. Reserving a **price** for one customer: a row with an `expires_at`. Trivial.
2. Reserving **risk capacity** from a company-wide budget: because 10,000 locked quotes are a real,
   aggregate market exposure that treasury has to manage whether or not any individual one is funded.

**Probability-weighted hedging.** *Hedging* = making an offsetting trade so a market move cannot hurt
you. If you hedge 100% of locked quotes, you are hedging a lot of transfers that will never happen —
and then you have to unwind those hedges at a loss when the quotes expire. So you hedge
`notional × P(this quote actually gets funded)`, and rebalance as reality resolves. Being able to say
"the drop-off rate is not just a funnel metric, it is a hedging input" ties §3 and §9 together, which
is exactly the kind of connection interviewers score.

**Circuit breaker.** If the rate feed is stale, or exposure is too big, stop offering the lock and
fall back to live quotes. Degrade the *product*, never the *price*: a customer who cannot get a
24-hour lock is mildly annoyed; a customer quoted a price from a frozen feed is a loss you cannot
recover.

**Lazy expiry (§3.4) — the strongest single idea in the document.**

The tempting design is a cron job that marks quotes expired. The bug: if the job is stuck, dead, or
lagging by ten minutes, a quote that expired at 09:00 is still `LOCKED` at 09:07 — and someone funds
it at yesterday's price. Your money correctness now depends on a scheduler's uptime.

The fix is to check expiry **at the moment of use**, inside the same transaction that consumes it:

```sql
SELECT * FROM quotes
 WHERE id = $1 AND state = 'LOCKED' AND expires_at > now()
   FOR UPDATE;      -- no row → 409 QUOTE_EXPIRED, deterministically
```

Now the cron job can be down for a week and nothing mis-prices; you only get stale hedge reservations
(a dashboard problem). Generalise it: **never let correctness depend on a background job; let the
job do hygiene, and let the read path enforce the invariant.**

Two supporting mechanics:

- `FOR UPDATE` locks the row so two concurrent requests cannot both consume one quote. The second
  waits, then sees `state = 'CONSUMED'` and fails cleanly.
- `FOR UPDATE SKIP LOCKED` in the sweeper means several workers can process the expiry backlog
  concurrently, each grabbing rows the others have not locked — a work queue in plain SQL, no
  Redis, no distributed lock, no chance of two workers doing the same row.

### §4 The state machine

**Why a state machine at all.** A transfer is a long-lived process (minutes to days) driven by
events from outside your control — the customer's bank, the sanctions screener, the partner bank.
Booleans (`is_funded`, `is_sent`) rot immediately, because they permit nonsense combinations like
`is_sent && !is_funded`. One `state` column with declared legal transitions makes the illegal states
unrepresentable.

**The states the brief did not ask for.** `CREATED → FUNDS_RECEIVED → CONVERTED →
OUTBOUND_DISPATCHED → DELIVERED` is the path where everything works. Real systems spend their
engineering budget elsewhere:

| State | The real situation |
|---|---|
| `PARTIALLY_FUNDED` | Sent £990 instead of £1,000, or their bank took a fee off the top |
| `COMPLIANCE_HOLD` | Something tripped a rule; a human must look |
| `RETURNED` | Ben's bank bounced it — closed account, wrong IBAN |
| `FROZEN` | Genuine sanctions match; we may be legally barred from giving it back |
| `EXPIRED` | The quote timed out and the money never came |
| `REFUND_PENDING` / `REFUNDED` | Going back to Alice |

Naming these unprompted is the single easiest way to look like you have operated a payment system
rather than read about one.

**`DELIVERED` vs `SETTLED` (§4.2).** SEPA Instant sends back a positive confirmation in ~10 seconds:
you *know* it arrived. Classic SEPA does not — you know your account was debited, and you know no
rejection came back within a few days. Those are different facts, so they are different states, and
the customer-facing API collapses them. The principle: **do not encode a guess as a fact.** Telling
someone their rent arrived when it later bounces is a worse failure than telling them "sent, arriving
by Tuesday".

**Three layers of enforcement (§4.3).**

1. **A transitions table.** Legal edges are rows, and `transfer_events` has a foreign key to them.
   The *database* now rejects `CREATED → DELIVERED`, including from a hand-typed `UPDATE` during an
   incident. Invariants in the database survive; invariants in application code survive until the
   next hotfix.
2. **Compare-and-swap.**
   ```sql
   UPDATE transfers SET state = 'CONVERTED'
    WHERE id = $1 AND state = 'FUNDS_RECEIVED';   -- 0 rows → someone beat me to it
   ```
   Read-modify-write is a race: two workers both read `FUNDS_RECEIVED`, both convert, and you have
   converted twice. Putting the expected state in the `WHERE` makes the check and the write one
   atomic operation, and the row count tells you who won. This is optimistic concurrency, and it is
   the whole reason duplicate event delivery is survivable.
3. **An append-only event log.** Every transition is a row: what changed, who caused it, which
   external message triggered it. `transfers.state` is a convenience projection of that log. When a
   regulator or an angry customer asks "what happened to my money and when", the answer is a query.

**Orchestration vs choreography (§4.4).** *Choreography*: each service listens for events and reacts,
with no central brain. *Orchestration*: one component owns the workflow and tells the others what to
do. Choreography is fashionable and genuinely good for loosely-coupled domains — but payments have
**asymmetric compensation**: you can un-reserve a quote, you cannot un-send a SEPA payment (you can
only request a recall and hope). When some steps are irreversible, the sequencing logic and the
compensations belong in one place you can read top to bottom. Otherwise "why is this transfer stuck?"
requires reading six services' event handlers.

### §5 Data model

**Why PostgreSQL, in one line each:**

- Atomic "state change + money movement" in a single transaction.
- Constraints are invariants the database enforces, not conventions developers remember.
- `FOR UPDATE SKIP LOCKED` gives queue semantics without a queue.
- Partitioning and replicas cover the volume, with a documented shard key if it ever ten-x's.

**Why not the alternatives** (say this proactively; it is a scored question):

| Rejected | Reason |
|---|---|
| Document store | No cross-document constraints, and money is inherently relational |
| Kafka as source of truth | Excellent transport, miserable when support asks "what is transfer X doing?" |
| Redis as authoritative | It is a cache for rate feeds; everything authoritative falls back to Postgres |

**Schema details worth defending:**

- `BIGINT` minor units (pennies, cents) and `NUMERIC(18,8)` rates. **Never floats.** `0.1 + 0.2`
  is famously not `0.3` in binary floating point, and a rounding error in a ledger is a break that a
  human has to investigate. Amounts cross the API as integers too — a JSON parser that turns
  `10.15` into a float undoes the discipline at the boundary.
- `payin_reference UNIQUE` — the code Alice types into her banking app, which is how we recognise
  her money when it lands.
- `end_to_end_id UNIQUE` — the SEPA-scheme reference, and therefore the database-level guarantee
  that a retry storm cannot send Ben two payments.
- Partial indexes (`WHERE state = 'LOCKED'`, `WHERE published_at IS NULL`). The sweeper and the
  outbox relay only ever query the tiny unfinished subset, so the index stays small forever while
  the table grows to hundreds of millions of rows.

### §6 Double-entry accounting

Fully unpacked in §B above. The three things to be able to say cold:

1. Journals balance **per currency**; a conversion is two legs joined by the `position.fx.*` pair.
2. Entries are **immutable**; corrections are reversals; balances are **derived** and reconciled.
3. **Reconciliation** is the real control: every day, compare our ledger against the bank's own
   statement (`camt.053`) for each account. Any mismatch opens a case. The state machine can be
   perfect and the partner bank can still be wrong; the only way to find out is to check.

### §7 Compliance

**KYC / AML in one line each.** *KYC* (Know Your Customer): prove the customer is who they claim.
*AML* (Anti-Money-Laundering): the wider obligation to detect and report money that is being cleaned
by moving it around. *Sanctions screening*: check both parties against government lists of people and
entities you are legally forbidden to transact with.

**The split, and the actual criterion.** Not "fast vs slow" — the real question is **what does
blocking cost, versus what does being wrong cost?**

| | Runs | Contents |
|---|---|---|
| **Synchronous** | Before we give Alice payment instructions | KYC status, sanctions screening, embargoed corridors, velocity limits, duplicate detection |
| **Asynchronous** | While Alice walks to her banking app | ML risk scoring, monitoring typologies, re-screening against refreshed lists, travel-rule enrichment |

**Why the sync gate runs *before* we take the money** — this is the point of the whole section.
Refusing a payment costs a support ticket. Accepting money we are not allowed to move and then
trying to give it back costs weeks, sometimes involves a regulator, and is occasionally *illegal*
(you may be required to freeze it, not return it). So the cheap, certain, legally-absolute checks
happen while refusing is still free.

**Why the slow checks are async, and free.** The ML score needs feature lookups and a model call;
gating creation on it would be slow *and* noisy. But we do not need the answer at creation — we need
it before **dispatch**, and between those two moments sits the human being walking to their banking
app. The latency is hidden behind the user, which is the best kind of free.

**Fuzzy matching and false positives.** Names transliterate ("Mohammed"/"Muhammad"), so screening is
similarity-scored, not exact. Set the threshold loose and you drown ops in false positives; set it
tight and you miss a real hit. Hence the *good-guy list*: once a human has cleared "this customer is
not that person on the list", the same pairing auto-clears next time. Ops cost is a design
constraint, not an afterthought.

**Fail closed.** If the risk scorer is down, transfers go to `COMPLIANCE_HOLD` — they do not sail
through. The asymmetry is not close: a delayed transfer is a support ticket, an unscreened payout to
a sanctioned party is your licence. This is where §1's "correctness over availability" cashes out,
and the interviewer is checking whether you will trade a compliance control for a latency SLO.

**`FROZEN` and the ugly truth.** A true sanctions match *after* funding means we hold money we may be
legally forbidden to return, and must report. So: its own terminal state, its own ledger account,
no automated exit, four-eyes approval for any human action, every step in the immutable log. Good
ops tooling here is product work, not a side-quest.

### §8 Pay-in and payout

**Attribution — the unglamorous problem that dominates support tickets.** £1,000 lands in our
account. *Which of the 12,000 open transfers is it for?* We try the reference code, then a
confidence-scored match on amount plus sender, and if neither works the money goes to a **suspense
account** and a human decides. Never guess with money. (This is also why Open Banking beats a manual
bank transfer: the payment is initiated through us, so we get an ID and attribution is near-perfect.)

**Amount mismatches**, which happen constantly:
- Slightly short (their bank took a fee): within tolerance, proceed and recompute the euro amount
  at the same locked rate.
- Materially short: `PARTIALLY_FUNDED`, ask for a top-up, quote clock keeps running.
- Overpaid: pay the quoted amount and refund the excess, or offer a re-quote — a locked rate covers
  a locked *amount*, not any amount.

**The SEPA vocabulary**, decoded:

| Term | What it is |
|---|---|
| **SEPA** | The single euro payments area: euro transfers across ~36 countries work like domestic ones |
| **SCT** | SEPA Credit Transfer — the classic one. Business hours, cut-off times, up to one business day |
| **SCT Inst** | SEPA Instant — 24/7/365, ≤10 seconds, and it confirms delivery |
| **IBAN / BIC** | The account identifier / the bank identifier |
| **ISO 20022** | The XML message standard the whole thing speaks |
| `pacs.008` | "Please make this payment" — the instruction we send |
| `pacs.002` | "Accepted" / "rejected" — the response |
| `pacs.004` | "Returned" — it bounced back, with a reason code (`AC01` bad account, `AC04` closed…) |
| `camt.056` | "Please send that one back" — a recall request |
| `camt.053` | Yesterday's full statement — the source of truth for reconciliation |
| **EndToEndId** | Our own reference, carried untouched through the scheme |
| **VoP** | Verification of Payee: does the name match the IBAN? Mandatory in the EU since Oct 2025 |

**`EndToEndId` is the load-bearing one.** It is an idempotency key *inside the payment scheme*.
When the partner bank times out and you do not know whether the payment went through, you do not
retry — you **query by `EndToEndId`** and find out. "Unknown outcome" is a state to resolve, never a
reason to send again. Blind retries on an unknown payment outcome is how people get paid twice.

**Liquidity.** Payouts come from the pre-funded EUR pile, so the pile can run dry. Hence a forecast,
a low-water alarm, and a dispatcher that throttles rather than firing payments that will bounce for
insufficient funds. Liquidity is a system input; ignore it and everything works beautifully until
month-end.

### §9 Drop-offs — the question in the brief

*"What if the user gets a quote and never sends the money?"* It happens to a large share of quotes,
so it is a first-class flow, not an error path.

**The timeline.** Created → reminder at +2 h → "your rate expires in 4 hours" at +20 h (the
highest-converting message in the funnel) → at +24 h the quote expires, the hedge reservation is
released, and the customer gets a one-tap re-quote.

**Keep the shell, do not delete it.** The transfer object holds the recipient's bank details, a
passed KYC gate, a completed screening result and the travel-rule payload — all expensive to
reproduce. Keeping it is what makes the comeback flow one tap instead of a re-onboarding. `EXPIRED`
is terminal for that *quote*, not for the relationship.

**Money that arrives after expiry** — the case that separates a real answer from a textbook one. The
funds are legitimately ours to hold, but the promised price is gone. Three policies:

- Within a tolerance band (rate moved only slightly, amount modest): auto-re-quote at the live rate
  and carry on. And if the live rate is *better* for the customer, give them the better one — cheap
  goodwill, and it removes any incentive to game the timing.
- Outside the band: hold the money, ask them to accept the new rate or take a refund. **Never
  silently re-price upward.**
- No answer in 5 days: refund to the account the money came from — running through the state machine
  and the ledger like any other movement, because a refund is a payment too.

**"Cleanup" never means `DELETE`.** Financial records are retained for years by law. Expired rows
get state transitions and partition rotation, and eventually cold storage. Anyone who answers this
question with "a job deletes stale quotes" has just deleted evidence.

### §10 Failure modes

The table is the "have you actually run one of these" section. The pattern behind every row:
**assume every message arrives twice, out of order, or never — and make the outcome correct anyway.**

The three worth memorising:

- **Duplicate `POST /transfers`** (user double-taps): an `Idempotency-Key` header, stored with a
  hash of the request body. Same key + same body → replay the stored response. Same key + different
  body → `409`. The user gets one transfer no matter how many times they tap.
- **Timeout on dispatch**: query by `EndToEndId`, never blind-retry. (Above.)
- **Ledger imbalance detected**: stop the line. Page a human, halt dispatch on the affected account,
  reconcile before resuming. Every other bug can wait for business hours; this one cannot, because
  every subsequent transaction builds on a foundation you now know is wrong.

**Observability.** The one to lead with: **per-state age alerts.** A transfer sitting in `CONVERTED`
for thirty minutes is an incident *before* any customer notices. Alerting on the age of things in
intermediate states — rather than on error rates — is what catches the silent stalls, which in a
payment system are the expensive ones.

### §11 API

Standard REST, with the payment-specific bits: an `Idempotency-Key` header on anything that moves
money, integer minor units everywhere, and webhooks that are **hints, not truth** — signed, retried,
at-least-once, out of order, so consumers are told to read the resource back rather than trust the
payload. Any integration that assumes exactly-once webhooks breaks during your first retry storm.

### §12 Trade-offs

Every row is a decision, its alternative, and why. Two are worth arguing rather than reciting:

- **One database vs microservices with sagas.** The counter-argument is scale and team autonomy; the
  answer is that at 40 tps the atomicity is worth more than the independence, and the module
  boundaries mean the split remains available later. "I would split it when a specific pressure
  demands it, and here is what that pressure would look like" beats both dogmas.
- **Fail closed on compliance.** Non-negotiable, and worth saying so plainly.

The closing paragraph — what I would build in one quarter — matters more than it looks. It shows you
can sequence, and it names the two things that are genuinely painful to retrofit: **the ledger and
the state machine**. Everything else can be added incrementally; those two are foundations.

---

## D. Jargon dictionary

**Payments**

| Term | Meaning |
|---|---|
| Corridor | A country/currency route, e.g. UK→DE, GBP→EUR |
| Pay-in / payout | Money coming from the sender / going to the recipient |
| FPS (Faster Payments) | The UK's instant domestic transfer scheme |
| Open Banking | Initiating a payment on the user's behalf via their bank's API — better attribution |
| Pre-funding | Putting our money in a country in advance so we can pay out immediately |
| Nostro | Our account held at another bank (industry term for the EUR pile) |
| Settlement | The moment money actually leaves or enters a bank account |
| Cut-off time | The daily deadline after which a payment goes out the next business day |
| T+1 / T+2 | Settles one / two business days after the trade |
| Recall | A request to send a completed payment back |
| Return | The receiving bank rejecting a payment, sending it back |

**FX**

| Term | Meaning |
|---|---|
| Mid rate | The midpoint between buy and sell in the market — the "real" rate |
| Spread | The margin between the mid rate and the rate we quote; our revenue |
| bps (basis point) | One hundredth of a percent. 25 bps = 0.25% |
| Notional | The size of an exposure, in currency units |
| Long / short | Holding a currency / owing a currency you do not yet have |
| Position | Net long-or-short exposure in a currency |
| Hedge | An offsetting trade so a market move cannot hurt you |
| Flatten / unwind | Close a position back to zero |
| Slippage | The difference between the price you assumed and the price you got |
| Adverse selection | The people who take your offer are disproportionately those it loses money on |
| Volatility | How much the rate moves; the main input to what a 24-hour lock should cost |

**Compliance**

| Term | Meaning |
|---|---|
| KYC | Know Your Customer — identity verification |
| AML | Anti-Money-Laundering — the regime for detecting laundered funds |
| EDD | Enhanced Due Diligence — deeper checks for higher-risk cases |
| Sanctions list | Government list of parties you may not transact with (OFAC US, HMT UK, EU, UN) |
| Screening | Fuzzy-matching names against those lists |
| False positive | An innocent customer matching a list entry by name similarity |
| Transaction monitoring | Watching patterns over time for laundering typologies |
| Structuring | Splitting a big transfer into small ones to stay under thresholds |
| Mule account | An account used to receive and pass on other people's illicit funds |
| SAR | Suspicious Activity Report — the filing to the authorities |
| Travel rule | The obligation to attach sender and recipient details to a transfer |
| Four-eyes | Two authorised humans must approve an action |

**Engineering**

| Term | Meaning |
|---|---|
| Idempotent | Doing it twice has the same effect as doing it once |
| Idempotency key | A client-supplied ID that lets the server recognise a retry |
| At-least-once | Delivery guarantee: never lost, sometimes duplicated |
| Outbox | Publishing events via a DB table so publish and commit are atomic |
| CDC | Change Data Capture — streaming a database's changes as events |
| CAS | Compare-and-swap: write only if the value is still what I read |
| Optimistic concurrency | Assume no conflict; detect it at write time (CAS) instead of locking |
| `FOR UPDATE` | Lock these rows until my transaction ends |
| `SKIP LOCKED` | Skip rows others have locked — a work queue in SQL |
| Deferred constraint | A check run at `COMMIT` rather than per statement |
| Saga | A long-running workflow with explicit compensating actions |
| Projection | A queryable table derived from an event log |
| Partial index | An index over only the rows matching a condition — small and hot |
| Minor units | Integer pennies/cents; the only safe way to store money |
| p99 | The latency 99% of requests are faster than |
| SLO | The target you commit to for a metric like latency or availability |
| Fail closed / open | On failure, deny (safe) / allow (available) |

---

## E. Delivering it in an interview

### E.1 Do not start with the schema

The order that works, and roughly the time to spend in a 45-minute slot:

1. **Clarify (3 min).** One corridor or many? Are we licensed and pre-funded in the euro area? Is
   the 24-hour lock free? Volume? — Then state your assumptions and move.
2. **Frame the money (4 min).** §A.2, on the whiteboard: two piles, three steps, nothing crosses a
   border. Everything else hangs off this.
3. **Happy path end to end (8 min).** Alice → quote → pay-in → convert → SEPA → Ben, naming the
   state at each step.
4. **The ledger (7 min).** Double entry, per-currency balance, the `position.fx.*` pair. Write the
   conversion journal out; it is the highest-signal artefact you can put on the board.
5. **The three hard parts (15 min).** Rate lock and expiry; compliance sync/async and the dispatch
   gate; drop-offs and money-after-expiry.
6. **Failure and ops (5 min).** Idempotency, `EndToEndId`, at-least-once, per-state age alerts,
   reconciliation.
7. **Trade-offs and v1 scope (3 min).** What you would cut, and what you would never retrofit.

### E.2 Sentences worth having ready

- "Nothing crosses a border — we collect locally, rewrite our own books, and pay out from liquidity
  we already hold."
- "A 24-hour locked rate is a free option we have written to the customer, so the price has to cover
  adverse selection, not just volatility."
- "Expiry is enforced at the point of use, not by a cron job — correctness must not depend on a
  scheduler being alive."
- "Journals balance per currency; the conversion is two legs joined by an FX position account."
- "Dispatch is the point of no return, so that is where the compliance gate goes — and it fails
  closed."
- "The five happy-path states are the easy half; `PARTIALLY_FUNDED`, `RETURNED` and `FROZEN` are
  where the cost is."
- "Forty writes a second is small. I am spending the complexity budget on correctness, not
  sharding."

### E.3 Follow-ups you should expect

| Question | The short answer |
|---|---|
| "Why not microservices with a saga?" | Atomic state + ledger beats independence at this volume; boundaries preserved so we can split under real pressure |
| "How do you prevent double payment?" | Idempotency key at the API, CAS on transitions, unique `EndToEndId` at the scheme — three independent layers |
| "What if the rate moves 10% in the lock window?" | Circuit breaker withdraws the lock tier for new quotes; existing locks are honoured and are exactly what the hedge is for |
| "How do you scale this 100×?" | Partition by month, read replicas, then shard by `transfer_id` keeping the ledger whole — but I would not build it before the pressure exists |
| "The recipient's name is misspelled — what happens?" | VoP flags it before dispatch; if it still goes and bounces, `pacs.004` → `RETURNED` → re-send to corrected details or refund at the live rate |
| "How do you know a transfer was delivered?" | Instant gives a positive confirmation; classic SEPA does not, so that state is inferred and revocable — and modelled honestly as such |
| "Where does the money sit if the user never pays?" | Nowhere — no money exists yet. Only the quote and the hedge reservation expire. That is the whole point of expiring before funding |
