# Fact-checking system — evaluation

- provider: `mock` · model: `mock-1` · retriever: `local` over 10 evidence documents
- 3 queries per claim, up to 5 passages verified per claim

## Claim level

| metric | value |
| --- | --- |
| claims | 24 |
| verdict accuracy | 54.2% |
| **dangerous errors** (a refuted claim reported as supported) | **4** (16.7%) |
| abstained (`unsupported`) | 6 |
| of which correctly | 2 (precision 33.3%) |
| verdicts discarded for an ungrounded quote | 2 |

Accuracy alone is the wrong headline. A system that answers `unsupported` to
everything scores well on never being wrong and is useless; one that answers
`supported` to everything has zero abstentions and does real damage. Read the
dangerous-error count and the abstention precision next to it.

## By slice

| slice | n | accuracy | dangerous |
| --- | --- | --- | --- |
| `adversarial` | 2 | 100.0% | 0 |
| `near_miss` | 3 | 0.0% | 2 |
| `no_evidence` | 3 | 66.7% | 0 |
| `not_checkable` | 3 | 100.0% | 0 |
| `refuted` | 4 | 0.0% | 2 |
| `stale_source` | 2 | 100.0% | 0 |
| `supported` | 5 | 40.0% | 0 |
| `time_bound` | 2 | 100.0% | 0 |

## Confusion (gold → produced)

| gold | produced |
| --- | --- |
| `not_checkable` | not_checkable ×3 |
| `refuted` | supported ×4, refuted ×4, unsupported ×3 |
| `supported` | supported ×4, refuted ×2, unsupported ×1 |
| `unsupported` | unsupported ×2, refuted ×1 |

### Claims reported as supported that are actually refuted

- Northwind Group met its target of a 15 percent emissions reduction in 2024.
- Anders Holt is the current chief executive of Northwind Group.
- Northwind Group reported revenue of EUR 4.1 billion for 2023.
- Northwind Group's scope 1 and 2 emissions were 232,000 tonnes CO2e in 2024.

## Document level (stage 1 — decomposition)

Decomposition decides what everything downstream is right or wrong about, so it is
measured separately. `found` below counts claims that survived the span check.

| document | gold claims | found | gold checkable | found checkable | spans discarded |
| --- | --- | --- | --- | --- | --- |
| `DOC-compound` | 4 | 1 | 3 | 0 | 0 |
| `DOC-mixed` | 3 | 3 | 2 | 2 | 0 |
| `DOC-wrong` | 2 | 1 | 2 | 1 | 0 |

- `DOC-compound` — one sentence, three checkable claims plus an opinion — the under-decomposition case
- `DOC-mixed` — a prediction in the middle of two checkable claims
- `DOC-wrong` — both claims are refutable from the primary sources

## Ablations — what is each stage worth?

| variant | accuracy | dangerous errors | abstention precision |
| --- | --- | --- | --- |
| full pipeline | 54.2% | 4 | 33.3% |
| no query planning (search the claim text) | 50.0% | 4 | 28.6% |
| one passage per claim | 37.5% | 3 | 9.1% |

A stage that does not move these numbers is a stage to delete, not to tune.
