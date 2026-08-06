# 06 — Auction, Pacing & Budgets

Where predictions become money. This stage has a property the ML stages don't: **it must be
correct, not just good.** A 1% ranking regression costs revenue; a 1% budget bug costs refunds and
trust.

## 6.1 Auction mechanics

Ranked candidates carry a calibrated probability; the auction converts to expected value per mille:

```
eCPM_i = 1000 × bid_i × P(billable_event_i) × quality_i
```

| Pricing model | Billable event | eCPM |
|---|---|---|
| CPM | impression | `bid` |
| vCPM | viewable impression | `bid × pView` |
| CPC | click | `1000 × bid × pCTR` |
| CPA / target-CPA | conversion | `1000 × bid × pCTR × pCVR` |

`quality_i ∈ (0,1]` is a multiplier for creative quality, landing-page experience and policy
signals. It keeps low-quality high-bid ads from dominating — a long-run marketplace-health lever,
and worth naming explicitly because it's the difference between an auction and a race to the
bottom.

### Pricing rule

**Second-price (GSP) with a reserve** as the default, first-price for exchange-sourced demand:

```
price_winner = max(reserve, eCPM_2nd / (pCTR_1st × 1000)) + ε      # for CPC
```

Second-price makes truthful bidding roughly optimal, which reduces advertiser bid-shading games
and — importantly for us — makes **bids a more honest input to the model**. The industry has
largely moved to first-price for *exchange* auctions because of transparency in the supply chain;
for owned inventory second-price remains defensible. The design supports both via a per-placement
`pricing_rule` field, because this is a business decision, not an engineering one.

**Reserve prices** are set per placement from a quantile of the recent eCPM distribution
(recomputed hourly, floored by a manual minimum). Reserves are the highest-leverage revenue knob
in the whole system and should be A/B tested like any model change.

### Selection is not just argmax eCPM

The winner must additionally satisfy, checked in this order (cheapest-first, short-circuiting):

1. **Frequency cap** — `impressions(user, campaign, window) < cap`
2. **Budget available** — the campaign holds a valid spend lease (§6.3)
3. **Pacing admission** — the campaign's pacing controller admits this request (§6.2)
4. **Policy/brand safety** — creative approved for this placement and context
5. **Competitive separation** — no competing-advertiser ad in the same slot group

If the top candidate fails, fall through the ranked list (bounded to ~20 attempts, then no-fill).
Each rejection is counted **by reason** — `budget_exhausted`, `freq_capped`, `paced_out`,
`policy_blocked` — which is what makes "why isn't my campaign delivering?" answerable in the
console instead of a support ticket.

## 6.2 Pacing

Without pacing, a campaign with a €1,000 daily budget and a high bid spends it in the first 20
minutes on whatever traffic happens to be online then — bad for the advertiser (biased audience)
and bad for the marketplace (thin auctions the rest of the day).

**Mechanism: probabilistic admission with a PID controller per campaign.**

```
target_spend(t)   = daily_budget × traffic_fraction_elapsed(t)      # not linear time —
                                                                    # uses the diurnal curve
error(t)          = target_spend(t) − actual_spend(t)
admission_rate    = clamp(admission_rate + Kp·e + Ki·∫e + Kd·de/dt, 0.001, 1.0)
admit             = rand() < admission_rate
```

Notes that matter in practice:

- **`traffic_fraction_elapsed` uses the historical hourly traffic curve, not wall-clock time.**
  Pacing linearly against time over-spends at night and under-spends at peak.
- Controller state updates every ~10 s from spend telemetry; the *serving* check is a local
  float comparison (~ns). No remote call on the hot path.
- Integral windup is clamped, and the controller is reset at flight boundaries.
- For **accelerated** delivery campaigns, `admission_rate = 1.0` and only the budget lease applies.
- Alternative considered: **bid shading / multiplier pacing** (scale the bid down instead of
  dropping requests). It's smoother and preserves the auction's information, but it distorts price
  and interacts badly with second-price semantics. Probabilistic admission is simpler to reason
  about and to explain to an advertiser. A hybrid (shade first, then throttle) is the natural
  evolution.

## 6.3 Distributed budget control

**The hard constraint:** total spend across two active-active regions must not exceed budget by
more than 0.5%. A synchronous global counter is impossible at 45 ms p99 across regions.

**Solution: lease-based sub-allocation** (the same pattern as ID-range allocation).

```mermaid
sequenceDiagram
    participant P as Serving pod
    participant R as Regional budget service
    participant G as Global allocator
    participant L as Ledger (Iceberg/PG)

    G->>R: grant region quota (e.g. 55% of remaining, TTL 60s)
    P->>R: request lease(campaign, €5, TTL 30s)
    R-->>P: lease granted
    Note over P: spends locally, no network per impression
    P->>R: report actual spend (batched, 1s)
    R->>G: report region spend (5s)
    G->>G: rebalance quotas by regional burn rate
    L-->>G: authoritative spend (from impression ledger)
    G->>G: reconcile; shrink quotas as budget nears exhaustion
```

Key properties:

- **Lease sizes shrink as the budget depletes.** Early in the day: €50 leases, coarse and cheap.
  At <5% remaining: €0.10 leases with synchronous regional checks. Overspend risk is bounded by
  `outstanding_leases`, which is deliberately made small exactly when it matters.
- **Leases expire.** A pod that dies holding a lease releases it after TTL — worst case is
  *under*-delivery for 30 s, which is the safe direction.
- **The ledger is the truth**, not the counters. Impressions are written to a durable,
  deduplicated ledger; a reconciliation job compares ledger spend to counter spend every 5 minutes
  and corrects. Counters are an optimization; the ledger is the record.
- **Failure mode is fail-closed:** if the budget service is unreachable and the pod's lease has
  expired, the campaign is *not* served. Under-delivery is recoverable; overspend is a refund.

## 6.4 Frequency capping

Softer constraint — ~1% over-delivery on caps is tolerable, so it gets a cheaper mechanism.

```
key   = "fc:{user_id}:{campaign_id}"        # or a Count-Min sketch for cap>10 cases
value = counter with TTL = cap window
```

- Written asynchronously on impression, read on the hot path from the regional TTL store *and* an
  in-process LRU (users repeat within a session, so hit rate is high).
- **Cross-region:** counters replicate asynchronously (~1–2 s). A user hitting both regions inside
  that window can see one extra impression. Accepted, documented, and measured (`freq_cap_overrun`
  metric) rather than pretended away.
- For high-cap or budget-of-attention style caps, a **Count-Min sketch** per user keeps memory
  bounded at the cost of over-counting only (never under-counting) — the safe error direction.

## 6.5 Billing integrity

Ad requests can be lossy; **billing events cannot**.

| Property | Mechanism |
|---|---|
| No lost impressions | Impression beacons hit a dedicated endpoint that writes to Kafka with `acks=all` before returning 204; SDK retries on failure with exponential backoff and `sendBeacon` on unload |
| No double-billing | Every impression carries a signed, single-use `impression_id`; the ledger dedupes on it with a 7-day window (RocksDB-backed Flink keyed state) |
| No fraudulent billing | The `ad_token` returned in `AdResponse` is an HMAC over `(impression_id, campaign_id, price, exp)`; the beacon endpoint verifies signature + expiry + one-time use. Prices are never taken from the client. |
| Invalid traffic | Post-hoc IVT classification (bot signatures, impossible timing, datacenter IPs) marks events; billing runs on the filtered set; advertisers are credited in the next cycle |
| Auditability | Append-only ledger in Iceberg, partitioned by hour, with the full auction context (all bids, prices, reserve, model version) for every impression — required to answer advertiser disputes |

## 6.6 What could go wrong

| Failure | Impact | Mitigation |
|---|---|---|
| Model over-predicts pCTR for one campaign | Systematic overbidding, budget burns fast | Calibration monitors per campaign; pacing bounds daily damage; anomaly alert on spend velocity |
| Budget service partition | Leases expire, campaigns stop | Fail-closed by design; alert; regional quotas have a grace-mode with reduced lease sizes |
| Clock skew between regions | Pacing/flight boundaries misalign | NTP + all times in UTC epoch millis; flight boundaries evaluated with a 60 s tolerance band |
| Reserve set too high | Fill rate collapses | Fill-rate SLO alert (< 0.5% unexplained no-fill), automatic reserve rollback |
| Feedback loop: model learns from its own biased choices | Quality decays slowly | Exploration slot + IPS-weighted evaluation ([12](./12-observability-and-experimentation.md)) |

---
Next: [07 — Backend LLD](./07-backend-lld.md)
