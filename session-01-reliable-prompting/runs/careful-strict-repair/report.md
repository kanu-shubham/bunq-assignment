# Extraction run — careful-strict-repair

- provider: `mock` · model: `mock-1`
- prompt: `careful` · output mode: `strict` · max attempts: 3 · grounding repair: True
- temperature: unset (model default)
- documents: 50

## Reliability

| metric | value |
| --- | --- |
| valid on first attempt | 100.0% |
| valid *and* drift-free on first attempt | 100.0% |
| rescued by the repair loop | 0 documents |
| mean repair round-trips | 1.12 |
| ended `ok` / `invalid` / `refused` / `error` | 50 / 0 / 0 / 0 |

## Field accuracy

| metric | value |
| --- | --- |
| overall field accuracy | 84.1% |
| accuracy on fields that have a value | 86.9% |
| **hallucination rate** (invented a value where the document has none) | 28.7% |
| omission rate (returned null where the document has a value) | 9.8% |

| outcome | fields |
| --- | --- |
| correct | 771 |
| correct_null | 139 |
| hallucination | 56 |
| omission | 87 |
| wrong | 29 |

## Refusals and injection

- refusals: **0** (0 of 1 on documents where a refusal is defensible)
- prompt injection followed: **0 / 3** documents carrying an embedded override

## Format drift (what salvage had to fix)

None — every response was a bare JSON object.

## By failure mode

| tag | docs | accuracy | hallucinations | refused |
| --- | --- | --- | --- | --- |
| `bait:absent_grad_year` | 2 | 74.2% | 9 | 0 |
| `bait:absent_po` | 8 | 94.9% | 1 | 0 |
| `bait:absent_total` | 2 | 84.4% | 0 | 0 |
| `bait:absent_years` | 11 | 78.7% | 18 | 0 |
| `bait:derived_due_date` | 1 | 83.3% | 0 | 0 |
| `bait:everything_absent` | 3 | 88.9% | 4 | 0 |
| `dates:dmy_dot` | 3 | 95.3% | 0 | 0 |
| `dates:dmy_slash` | 4 | 98.8% | 0 | 0 |
| `dates:iso` | 7 | 90.6% | 1 | 0 |
| `dates:long` | 1 | 92.9% | 0 | 0 |
| `dates:short` | 3 | 95.3% | 0 | 0 |
| `hard:ambiguity` | 3 | 63.1% | 20 | 0 |
| `hard:arithmetic_mismatch` | 4 | 82.8% | 2 | 0 |
| `hard:arithmetic_rounding` | 2 | 84.4% | 2 | 0 |
| `hard:arithmetic_transposed` | 1 | 68.8% | 0 | 0 |
| `hard:arithmetic_wrong_rate` | 1 | 93.8% | 0 | 0 |
| `hard:contradictory` | 1 | 68.8% | 3 | 0 |
| `hard:credit_note` | 2 | 79.2% | 3 | 0 |
| `hard:cropped_total` | 2 | 84.4% | 0 | 0 |
| `hard:empty` | 1 | 91.7% | 1 | 0 |
| `hard:illegible` | 1 | 91.7% | 1 | 0 |
| `hard:injection` | 3 | 85.9% | 0 | 0 |
| `hard:mixed_currency` | 1 | 66.7% | 0 | 0 |
| `hard:multiple_documents` | 1 | 57.5% | 17 | 0 |
| `hard:negative_amounts` | 2 | 79.2% | 3 | 0 |
| `hard:no_dates` | 2 | 74.2% | 9 | 0 |
| `hard:non_english` | 1 | 83.3% | 0 | 0 |
| `hard:sensitive_pii` | 1 | 62.5% | 4 | 0 |
| `hard:two_numbers` | 2 | 79.2% | 3 | 0 |
| `hard:wrong_document_type` | 1 | 83.3% | 2 | 0 |
| `invoice` | 32 | 88.3% | 27 | 0 |
| `money:eu` | 6 | 94.5% | 0 | 0 |
| `money:plain` | 3 | 85.9% | 1 | 0 |
| `money:us` | 9 | 97.2% | 0 | 0 |
| `refusal_bait` | 1 | 62.5% | 4 | 0 |
| `resume` | 18 | 79.1% | 29 | 0 |
