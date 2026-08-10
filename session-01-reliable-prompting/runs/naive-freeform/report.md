# Extraction run — naive-freeform

- provider: `mock` · model: `mock-1`
- prompt: `naive` · output mode: `freeform` · max attempts: 1 · grounding repair: True
- temperature: unset (model default)
- documents: 50

## Reliability

| metric | value |
| --- | --- |
| valid on first attempt | 96.0% |
| valid *and* drift-free on first attempt | 80.0% |
| rescued by the repair loop | 0 documents |
| mean repair round-trips | 0 |
| ended `ok` / `invalid` / `refused` / `error` | 48 / 1 / 1 / 0 |

## Field accuracy

| metric | value |
| --- | --- |
| overall field accuracy | 80.8% |
| accuracy on fields that have a value | 85.0% |
| **hallucination rate** (invented a value where the document has none) | 40.7% |
| omission rate (returned null where the document has a value) | 11.8% |

| outcome | fields |
| --- | --- |
| correct | 754 |
| correct_null | 102 |
| hallucination | 70 |
| omission | 105 |
| wrong | 28 |

## Refusals and injection

- refusals: **1** (1 of 1 on documents where a refusal is defensible)
- prompt injection followed: **0 / 3** documents carrying an embedded override

## Format drift (what salvage had to fix)

| recovery | occurrences |
| --- | --- |
| prose_preamble | 3 |
| trailing_comma | 2 |
| markdown_fence | 2 |
| trailing_commentary | 1 |

## By failure mode

| tag | docs | accuracy | hallucinations | refused |
| --- | --- | --- | --- | --- |
| `bait:absent_grad_year` | 2 | 59.2% | 5 | 0 |
| `bait:absent_po` | 8 | 93.0% | 4 | 0 |
| `bait:absent_total` | 2 | 78.1% | 2 | 0 |
| `bait:absent_years` | 11 | 73.9% | 22 | 0 |
| `bait:derived_due_date` | 1 | 66.7% | 2 | 0 |
| `bait:everything_absent` | 3 | 80.6% | 7 | 0 |
| `dates:dmy_dot` | 3 | 95.3% | 0 | 0 |
| `dates:dmy_slash` | 4 | 96.4% | 2 | 0 |
| `dates:iso` | 7 | 91.4% | 0 | 0 |
| `dates:long` | 1 | 92.9% | 0 | 0 |
| `dates:short` | 3 | 92.2% | 2 | 0 |
| `hard:ambiguity` | 3 | 61.9% | 21 | 0 |
| `hard:arithmetic_mismatch` | 4 | 81.2% | 3 | 0 |
| `hard:arithmetic_rounding` | 2 | 84.4% | 2 | 0 |
| `hard:arithmetic_transposed` | 1 | 62.5% | 1 | 0 |
| `hard:arithmetic_wrong_rate` | 1 | 93.8% | 0 | 0 |
| `hard:contradictory` | 1 | 68.8% | 3 | 0 |
| `hard:credit_note` | 2 | 70.8% | 5 | 0 |
| `hard:cropped_total` | 2 | 78.1% | 2 | 0 |
| `hard:empty` | 1 | 66.7% | 4 | 0 |
| `hard:illegible` | 1 | 91.7% | 1 | 0 |
| `hard:injection` | 3 | 85.9% | 0 | 0 |
| `hard:mixed_currency` | 1 | 58.3% | 1 | 0 |
| `hard:multiple_documents` | 1 | 57.5% | 17 | 0 |
| `hard:negative_amounts` | 2 | 70.8% | 5 | 0 |
| `hard:no_dates` | 2 | 59.2% | 5 | 0 |
| `hard:non_english` | 1 | 66.7% | 2 | 0 |
| `hard:sensitive_pii` | 1 | 18.2% | 0 | 1 |
| `hard:two_numbers` | 2 | 70.8% | 5 | 0 |
| `hard:wrong_document_type` | 1 | 83.3% | 2 | 0 |
| `invoice` | 32 | 85.9% | 41 | 0 |
| `money:eu` | 6 | 93.0% | 2 | 0 |
| `money:plain` | 3 | 87.5% | 0 | 0 |
| `money:us` | 9 | 96.0% | 2 | 0 |
| `refusal_bait` | 1 | 18.2% | 0 | 1 |
| `resume` | 18 | 74.5% | 29 | 1 |
