# Extraction run — careful-freeform

- provider: `mock` · model: `mock-1`
- prompt: `careful` · output mode: `freeform` · max attempts: 1 · grounding repair: True
- temperature: unset (model default)
- documents: 50

## Reliability

| metric | value |
| --- | --- |
| valid on first attempt | 100.0% |
| valid *and* drift-free on first attempt | 80.0% |
| rescued by the repair loop | 0 documents |
| mean repair round-trips | 0 |
| ended `ok` / `invalid` / `refused` / `error` | 50 / 0 / 0 / 0 |

## Field accuracy

| metric | value |
| --- | --- |
| overall field accuracy | 83.8% |
| accuracy on fields that have a value | 86.9% |
| **hallucination rate** (invented a value where the document has none) | 30.3% |
| omission rate (returned null where the document has a value) | 9.6% |

| outcome | fields |
| --- | --- |
| correct | 771 |
| correct_null | 136 |
| hallucination | 59 |
| omission | 85 |
| wrong | 31 |

## Refusals and injection

- refusals: **0** (0 of 1 on documents where a refusal is defensible)
- prompt injection followed: **0 / 3** documents carrying an embedded override

## Format drift (what salvage had to fix)

| recovery | occurrences |
| --- | --- |
| prose_preamble | 4 |
| markdown_fence | 3 |
| trailing_comma | 2 |
| trailing_commentary | 1 |

## By failure mode

| tag | docs | accuracy | hallucinations | refused |
| --- | --- | --- | --- | --- |
| `bait:absent_grad_year` | 2 | 75.8% | 8 | 0 |
| `bait:absent_po` | 8 | 94.9% | 1 | 0 |
| `bait:absent_total` | 2 | 78.1% | 2 | 0 |
| `bait:absent_years` | 11 | 78.7% | 18 | 0 |
| `bait:derived_due_date` | 1 | 83.3% | 0 | 0 |
| `bait:everything_absent` | 3 | 86.1% | 5 | 0 |
| `dates:dmy_dot` | 3 | 95.3% | 0 | 0 |
| `dates:dmy_slash` | 4 | 98.8% | 0 | 0 |
| `dates:iso` | 7 | 91.4% | 0 | 0 |
| `dates:long` | 1 | 92.9% | 0 | 0 |
| `dates:short` | 3 | 93.8% | 1 | 0 |
| `hard:ambiguity` | 3 | 63.1% | 20 | 0 |
| `hard:arithmetic_mismatch` | 4 | 82.8% | 2 | 0 |
| `hard:arithmetic_rounding` | 2 | 87.5% | 1 | 0 |
| `hard:arithmetic_transposed` | 1 | 62.5% | 1 | 0 |
| `hard:arithmetic_wrong_rate` | 1 | 93.8% | 0 | 0 |
| `hard:contradictory` | 1 | 68.8% | 3 | 0 |
| `hard:credit_note` | 2 | 79.2% | 3 | 0 |
| `hard:cropped_total` | 2 | 78.1% | 2 | 0 |
| `hard:empty` | 1 | 83.3% | 2 | 0 |
| `hard:illegible` | 1 | 91.7% | 1 | 0 |
| `hard:injection` | 3 | 85.9% | 0 | 0 |
| `hard:mixed_currency` | 1 | 66.7% | 0 | 0 |
| `hard:multiple_documents` | 1 | 57.5% | 17 | 0 |
| `hard:negative_amounts` | 2 | 79.2% | 3 | 0 |
| `hard:no_dates` | 2 | 75.8% | 8 | 0 |
| `hard:non_english` | 1 | 83.3% | 0 | 0 |
| `hard:sensitive_pii` | 1 | 62.5% | 4 | 0 |
| `hard:two_numbers` | 2 | 79.2% | 3 | 0 |
| `hard:wrong_document_type` | 1 | 83.3% | 2 | 0 |
| `invoice` | 32 | 87.8% | 30 | 0 |
| `money:eu` | 6 | 93.8% | 1 | 0 |
| `money:plain` | 3 | 87.5% | 0 | 0 |
| `money:us` | 9 | 97.2% | 0 | 0 |
| `refusal_bait` | 1 | 62.5% | 4 | 0 |
| `resume` | 18 | 79.1% | 29 | 0 |
