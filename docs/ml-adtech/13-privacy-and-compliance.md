# 13 — Privacy & Compliance

Ad-tech inside an EU-regulated financial institution has constraints that a generic ad platform
doesn't. Treating them as architecture (rather than as a legal review at the end) is cheaper and is
also a differentiator worth raising unprompted in an interview.

## 13.1 Constraints that shape the design

| Regulation | Requirement | Architectural consequence |
|---|---|---|
| **GDPR** | Lawful basis; purpose limitation; data minimization; right to erasure; right to explanation for automated decisions | Consent as a serving input; pseudonymous ids; TTL'd features; deletion propagates to lake + models |
| **ePrivacy** | Consent before storing/reading device identifiers | No identifier written before consent; contextual path must be fully functional standalone |
| **DSA (Art. 26/39)** | Ads must be clearly labelled; no ads based on profiling using **special-category data**; ad repository for very large platforms | Sensitive attributes are excluded at the *feature registry* level, not by convention; SDK labels ads visibly |
| **AI Act** (limited-risk here) | Transparency, documentation, risk management for the model | Model cards, documented eval, human oversight of ranking policy changes |
| **Financial-services rules** | Banking data cannot be used for ad targeting without explicit, separate consent | **Hard data-boundary between the core banking domain and the ads domain** |

**The single most important rule:** transaction data, balances, and account activity are *not*
inputs to ad ranking. Not "not by default" — not available. The ads feature store is a separate
system with separate credentials, and the only identity crossing the boundary is a salted,
rotating pseudonymous id. Attempting to join the two is a build-time failure (the ads feature
registry has no schema that could receive them), not a policy someone must remember.

## 13.2 Consent as a first-class serving input

```
ConsentState {
  personalization: bool     // may use user profile / behavioural features
  measurement:     bool     // may attribute conversions
  storage:         bool     // may persist an identifier
  tcf_string:      string   // IAB TCF v2.2, when applicable
}
```

Effect on the request path:

| Consent | Features used | Retrieval | Ranking |
|---|---|---|---|
| Full | user profile + realtime + context | two-tower personalized | full model |
| Measurement only | context + aggregate | contextual + popularity | context-only model input |
| None | context only (placement, page category, device class, coarse geo) | contextual | context-only model input |

The **contextual path is a first-class path, not a fallback**: it is the same code path with a
narrower feature set, it is load-tested, and its RPM is measured separately. Systems that treat
no-consent traffic as an error case discover at the worst moment that half of EU traffic is
"an error case".

Consent state is carried in the request, and it is also **logged with every row** so training can
respect it: rows without personalization consent do not train personalization features.

## 13.3 Data handling

| Control | Implementation |
|---|---|
| Pseudonymization | `user_id` = HMAC(stable_id, salt) with a salt rotated per epoch; no raw identifiers in the ads domain |
| Minimization | The feature registry requires a purpose + retention for every feature; features without a documented purpose fail review |
| Retention | Raw events 13 months (billing/audit), feature values 90 days, aggregates indefinitely, IP addresses truncated at ingest and dropped after 7 days |
| Right to erasure | Deletion request → tombstone into Kafka → (1) online store delete, (2) Iceberg partition rewrite via a scheduled compaction job, (3) exclusion from future training sets |
| Model-level erasure | Individual data is not extractable from an aggregate model; documented position + the retraining cadence (≤30 days) means erased users leave the model naturally. This is the honest answer; claiming per-user model unlearning would not be. |
| Special categories | Health, religion, politics, sexual orientation, ethnicity, and **financial-hardship proxies** are blocked at the registry; targeting rules referencing them fail validation in the campaign API |
| Cross-border | All processing in EU regions; no data leaves the EU; sub-processors documented |
| Access | Ads-domain data accessible only via RBAC with audit logging; production feature values are not queryable by humans in raw form (aggregates only) |

## 13.4 Transparency & fairness

- **Ad labelling** in the SDK: visible "Advertisement" text plus `aria-label`, and a "why am I
  seeing this?" affordance that returns the *targeting reason* (segment, placement, advertiser) —
  served from the logged auction context, not reconstructed.
- **Fairness monitoring:** delivery rates across protected-adjacent proxies (age band, coarse geo,
  device tier) are monitored for large disparities in *opportunity*, not just outcomes. Ad delivery
  systems can produce discriminatory delivery even with neutral targeting (the well-documented
  optimization-driven skew), so this needs measurement, not assumption.
- **Human oversight:** ranking-policy changes (objective weights, reserve rules, quality
  multipliers) go through review with a documented rationale; they are not silently tunable by an
  optimizer.
- **Model cards** per production model: intended use, training data window, eval slices, known
  limitations, fairness measurements.

## 13.5 Security

| Surface | Control |
|---|---|
| Ad response tampering | HMAC-signed `ad_token`; prices never client-supplied |
| Click fraud / bots | IVT classification, rate limits per key/IP/user, signature + one-time-use tokens |
| Publisher key abuse | Origin allowlist, per-key quotas, anomaly detection on fill/CTR |
| Creative security | Creatives sandboxed (`iframe` with restricted `sandbox`, CSP), no third-party JS in first-party slots, malware scanning at upload |
| Supply chain | Signed images (cosign), SBOM per build, pinned dependencies, no `latest` tags |
| Secrets | Workload identity + KMS, weekly HMAC key rotation with a dual-key verification window |
| Least privilege | Serving pods can read snapshots and write Kafka; they cannot read the campaign DB or the lake |

---
Next: [14 — Interview cheat sheet](./14-interview-cheatsheet.md)
