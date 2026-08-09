# Extraction run — careful-freeform

- provider: `mock` · model: `mock-1`
- prompt: `careful` · output mode: `freeform` · max attempts: 1 · grounding repair: True
- temperature: unset (model default)
- documents: 50

## Reliability

| metric | value |
| --- | --- |
| valid on first attempt | 94.0% |
| valid *and* drift-free on first attempt | 74.0% |
| rescued by the repair loop | 0 documents |
| mean repair round-trips | 0 |
| ended `ok` / `invalid` / `refused` / `error` | 47 / 3 / 0 / 0 |

## Field accuracy

| metric | value |
| --- | --- |
| overall field accuracy | 79.6% |
| accuracy on fields that have a value | 79.4% |
| **hallucination rate** (invented a value where the document has none) | 19.5% |
| omission rate (returned null where the document has a value) | 15.7% |

| outcome | fields |
| --- | --- |
| correct | 710 |
| correct_null | 124 |
| hallucination | 30 |
| omission | 140 |
| wrong | 44 |

## Refusals and injection

- refusals: **0** (0 of 1 on documents where a refusal is defensible)
- prompt injection followed: **0 / 3** documents carrying an embedded override

## Format drift (what salvage had to fix)

| recovery | occurrences |
| --- | --- |
| prose_preamble | 6 |
| markdown_fence | 2 |
| trailing_commentary | 1 |
| trailing_comma | 1 |

## By failure mode

| tag | docs | accuracy | hallucinations | refused |
| --- | --- | --- | --- | --- |
| `bait:absent_grad_year` | 2 | 81.5% | 5 | 0 |
| `bait:absent_po` | 10 | 91.3% | 2 | 0 |
| `bait:absent_total` | 2 | 61.4% | 0 | 0 |
| `bait:absent_years` | 10 | 74.2% | 12 | 0 |
| `bait:derived_due_date` | 1 | 83.3% | 1 | 0 |
| `bait:everything_absent` | 4 | 89.6% | 5 | 0 |
| `dates:dmy_dot` | 3 | 92.2% | 0 | 0 |
| `dates:dmy_slash` | 5 | 95.0% | 0 | 0 |
| `dates:iso` | 6 | 89.8% | 0 | 0 |
| `dates:long` | 1 | 89.3% | 0 | 0 |
| `dates:mdy_slash` | 1 | 95.0% | 0 | 0 |
| `dates:short` | 4 | 88.2% | 2 | 0 |
| `hard:ambiguity` | 3 | 46.3% | 1 | 0 |
| `hard:contradictory` | 1 | 88.9% | 1 | 0 |
| `hard:credit_note` | 2 | 66.7% | 1 | 0 |
| `hard:cropped_total` | 2 | 61.4% | 0 | 0 |
| `hard:empty` | 1 | 83.3% | 2 | 0 |
| `hard:illegible` | 2 | 91.7% | 2 | 0 |
| `hard:injection` | 3 | 74.6% | 3 | 0 |
| `hard:mixed_currency` | 1 | 58.3% | 0 | 0 |
| `hard:multiple_documents` | 1 | 0.0% | 0 | 0 |
| `hard:negative_amounts` | 2 | 66.7% | 1 | 0 |
| `hard:no_dates` | 2 | 81.5% | 5 | 0 |
| `hard:non_english` | 1 | 83.3% | 1 | 0 |
| `hard:sensitive_pii` | 1 | 76.9% | 4 | 0 |
| `hard:two_numbers` | 2 | 66.7% | 1 | 0 |
| `hard:wrong_document_type` | 1 | 91.7% | 1 | 0 |
| `invoice` | 31 | 82.5% | 9 | 0 |
| `money:eu` | 8 | 89.4% | 1 | 0 |
| `money:plain` | 3 | 90.6% | 0 | 0 |
| `money:us` | 9 | 93.6% | 1 | 0 |
| `refusal_bait` | 1 | 76.9% | 4 | 0 |
| `resume` | 19 | 76.2% | 21 | 0 |
