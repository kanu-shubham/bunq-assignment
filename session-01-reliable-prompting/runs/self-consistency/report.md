# Prototype 4 — self-consistency (majority vote over n samples)

- provider: `mock` · model: `mock-1`
- 50 documents × 5 samples · prompt `careful` · temperature 0.7

| | single sample | majority vote |
| --- | --- | --- |
| field accuracy | 83.2% | 84.0% |
| hallucination rate | 29.2% | 24.3% |
| omission rate | 11.1% | 11.1% |

Cost: 5× the tokens of a single call. Whether that is worth the
delta above is a product decision, not a technical one — but the margin signal
below comes free with it either way.

## Accuracy by vote margin

How often the majority answer is right, bucketed by how much of the majority there was.

| margin | fields | accuracy |
| --- | --- | --- |
| unanimous | 1036 | 83.8% |
| strong (≥75%) | 30 | 90.0% |
| split (≥50%) | 5 | 100.0% |
| scattered | 1 | 100.0% |

Unanimous fields are **83.8%** accurate; fields with any disagreement are **91.7%** accurate (36 fields).

The margin does **not** discriminate here — unanimous answers are no more accurate than contested ones. That means most of the remaining error is bias, not variance: the model is wrong the same way every time. Sampling more will not fix it and an abstain rule on the margin would mostly route correct fields to a human. Fix the prompt or the schema instead.

## Abstain rule: send fields with margin < 0.8 for review

- fields routed to a human: **6**
- of those, actually wrong: **0** (rule precision 0.0%)
- accuracy on the fields kept automatically: **84.0%**

## Documents the vote moved most

| doc | single | voted | delta | lowest margin |
| --- | --- | --- | --- | --- |
| `038-invoice-mixed_currency` | 58.3% | 66.7% | +8.3 pts | 0.8 |
| `040-invoice-wrong_kind` | 83.3% | 91.7% | +8.3 pts | 0.8 |
| `049-invoice-credit_note` | 75.0% | 83.3% | +8.3 pts | 0.4 |
| `048-resume-resume_no_dates` | 70.4% | 77.8% | +7.4 pts | 0.8 |
| `010-invoice-std` | 87.5% | 93.8% | +6.2 pts | 0.6 |
| `030-invoice-arith_rounding` | 87.5% | 93.8% | +6.2 pts | 0.8 |
| `046-invoice-arith_rounding` | 81.2% | 87.5% | +6.2 pts | 0.8 |
| `026-resume-std` | 77.1% | 80.0% | +2.9 pts | 0.8 |
| `000-invoice-std` | 100.0% | 100.0% | +0.0 pts | 1.0 |
| `001-invoice-std` | 100.0% | 100.0% | +0.0 pts | 1.0 |
