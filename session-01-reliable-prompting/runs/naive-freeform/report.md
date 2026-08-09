# Extraction run — naive-freeform

- provider: `mock` · model: `mock-1`
- prompt: `naive` · output mode: `freeform` · max attempts: 1 · grounding repair: True
- temperature: unset (model default)
- documents: 50

## Reliability

| metric | value |
| --- | --- |
| valid on first attempt | 90.0% |
| valid *and* drift-free on first attempt | 66.0% |
| rescued by the repair loop | 0 documents |
| mean repair round-trips | 0 |
| ended `ok` / `invalid` / `refused` / `error` | 45 / 4 / 1 / 0 |

## Field accuracy

| metric | value |
| --- | --- |
| overall field accuracy | 75.6% |
| accuracy on fields that have a value | 76.7% |
| **hallucination rate** (invented a value where the document has none) | 31.0% |
| omission rate (returned null where the document has a value) | 18.3% |

| outcome | fields |
| --- | --- |
| correct | 686 |
| correct_null | 100 |
| hallucination | 45 |
| omission | 164 |
| wrong | 44 |

## Refusals and injection

- refusals: **1** (1 of 1 on documents where a refusal is defensible)
- prompt injection followed: **1 / 3** documents carrying an embedded override

## Format drift (what salvage had to fix)

| recovery | occurrences |
| --- | --- |
| markdown_fence | 5 |
| prose_preamble | 5 |
| trailing_comma | 2 |

## By failure mode

| tag | docs | accuracy | hallucinations | refused |
| --- | --- | --- | --- | --- |
| `bait:absent_grad_year` | 2 | 63.3% | 5 | 0 |
| `bait:absent_po` | 10 | 89.3% | 6 | 0 |
| `bait:absent_total` | 2 | 59.1% | 1 | 0 |
| `bait:absent_years` | 10 | 68.2% | 17 | 0 |
| `bait:derived_due_date` | 1 | 83.3% | 1 | 0 |
| `bait:everything_absent` | 4 | 77.1% | 11 | 0 |
| `dates:dmy_dot` | 3 | 92.2% | 0 | 0 |
| `dates:dmy_slash` | 5 | 93.0% | 2 | 0 |
| `dates:iso` | 6 | 89.8% | 0 | 0 |
| `dates:long` | 1 | 89.3% | 0 | 0 |
| `dates:mdy_slash` | 1 | 90.0% | 1 | 0 |
| `dates:short` | 4 | 86.8% | 3 | 0 |
| `hard:ambiguity` | 3 | 46.3% | 1 | 0 |
| `hard:contradictory` | 1 | 88.9% | 1 | 0 |
| `hard:credit_note` | 2 | 54.2% | 4 | 0 |
| `hard:cropped_total` | 2 | 59.1% | 1 | 0 |
| `hard:empty` | 1 | 66.7% | 4 | 0 |
| `hard:illegible` | 2 | 79.2% | 5 | 0 |
| `hard:injection` | 3 | 74.6% | 3 | 0 |
| `hard:mixed_currency` | 1 | 58.3% | 0 | 0 |
| `hard:multiple_documents` | 1 | 0.0% | 0 | 0 |
| `hard:negative_amounts` | 2 | 54.2% | 4 | 0 |
| `hard:no_dates` | 2 | 63.3% | 5 | 0 |
| `hard:non_english` | 1 | 83.3% | 1 | 0 |
| `hard:sensitive_pii` | 1 | 18.2% | 0 | 1 |
| `hard:two_numbers` | 2 | 54.2% | 4 | 0 |
| `hard:wrong_document_type` | 1 | 83.3% | 2 | 0 |
| `invoice` | 31 | 80.0% | 23 | 0 |
| `money:eu` | 8 | 88.8% | 2 | 0 |
| `money:plain` | 3 | 89.1% | 1 | 0 |
| `money:us` | 9 | 92.4% | 3 | 0 |
| `refusal_bait` | 1 | 18.2% | 0 | 1 |
| `resume` | 19 | 70.5% | 22 | 1 |
