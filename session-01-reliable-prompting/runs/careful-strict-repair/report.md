# Extraction run — careful-strict-repair

- provider: `mock` · model: `mock-1`
- prompt: `careful` · output mode: `strict` · max attempts: 3 · grounding repair: True
- temperature: unset (model default)
- documents: 50

## Reliability

| metric | value |
| --- | --- |
| valid on first attempt | 98.0% |
| valid *and* drift-free on first attempt | 98.0% |
| rescued by the repair loop | 1 documents |
| mean repair round-trips | 1 |
| ended `ok` / `invalid` / `refused` / `error` | 50 / 0 / 0 / 0 |

## Field accuracy

| metric | value |
| --- | --- |
| overall field accuracy | 84.5% |
| accuracy on fields that have a value | 85.9% |
| **hallucination rate** (invented a value where the document has none) | 22.9% |
| omission rate (returned null where the document has a value) | 9.2% |

| outcome | fields |
| --- | --- |
| correct | 768 |
| correct_null | 128 |
| hallucination | 38 |
| omission | 82 |
| wrong | 44 |

## Refusals and injection

- refusals: **0** (0 of 1 on documents where a refusal is defensible)
- prompt injection followed: **0 / 3** documents carrying an embedded override

## Format drift (what salvage had to fix)

None — every response was a bare JSON object.

## By failure mode

| tag | docs | accuracy | hallucinations | refused |
| --- | --- | --- | --- | --- |
| `bait:absent_grad_year` | 2 | 83.3% | 4 | 0 |
| `bait:absent_po` | 10 | 91.3% | 2 | 0 |
| `bait:absent_total` | 2 | 88.6% | 0 | 0 |
| `bait:absent_years` | 10 | 82.4% | 10 | 0 |
| `bait:derived_due_date` | 1 | 83.3% | 1 | 0 |
| `bait:everything_absent` | 4 | 93.8% | 3 | 0 |
| `dates:dmy_dot` | 3 | 92.2% | 0 | 0 |
| `dates:dmy_slash` | 5 | 95.0% | 0 | 0 |
| `dates:iso` | 6 | 88.9% | 1 | 0 |
| `dates:long` | 1 | 89.3% | 0 | 0 |
| `dates:mdy_slash` | 1 | 90.0% | 1 | 0 |
| `dates:short` | 4 | 90.8% | 0 | 0 |
| `hard:ambiguity` | 3 | 74.7% | 13 | 0 |
| `hard:contradictory` | 1 | 88.9% | 1 | 0 |
| `hard:credit_note` | 2 | 66.7% | 1 | 0 |
| `hard:cropped_total` | 2 | 88.6% | 0 | 0 |
| `hard:empty` | 1 | 91.7% | 1 | 0 |
| `hard:illegible` | 2 | 91.7% | 2 | 0 |
| `hard:injection` | 3 | 76.3% | 2 | 0 |
| `hard:mixed_currency` | 1 | 58.3% | 0 | 0 |
| `hard:multiple_documents` | 1 | 70.0% | 12 | 0 |
| `hard:negative_amounts` | 2 | 66.7% | 1 | 0 |
| `hard:no_dates` | 2 | 83.3% | 4 | 0 |
| `hard:non_english` | 1 | 83.3% | 1 | 0 |
| `hard:sensitive_pii` | 1 | 76.9% | 4 | 0 |
| `hard:two_numbers` | 2 | 66.7% | 1 | 0 |
| `hard:wrong_document_type` | 1 | 100.0% | 0 | 0 |
| `invoice` | 31 | 88.0% | 19 | 0 |
| `money:eu` | 8 | 90.0% | 0 | 0 |
| `money:plain` | 3 | 87.5% | 2 | 0 |
| `money:us` | 9 | 94.2% | 0 | 0 |
| `refusal_bait` | 1 | 76.9% | 4 | 0 |
| `resume` | 19 | 80.4% | 19 | 0 |
