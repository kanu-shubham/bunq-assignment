# Prototype 5 — LLM as judge, scored against ground truth

- provider: `mock` · model: `mock-1`
- 49 of 50 extractions judged · 2 judging pass(es) · 0 judge call(s) failed
- extraction under test used the `naive` prompt, with grounding repair switched off so invented values survive for the judge to find

## The judge as an error detector

| detector | precision | recall | false alarms | fields |
| --- | --- | --- | --- | --- |
| LLM judge | 22.2% | 41.5% | 20.9% | 841 |
| `grounding.py` (no tokens) | 24.2% | 36.8% | 16.6% | 841 |

Precision is *of the fields the detector flagged, how many were really wrong*.
Recall is *of the fields that were really wrong, how many it caught*. A judge
that flags everything scores perfect recall and is worthless; read them together.

Judge confusion: 44 caught, 62 missed, 154 false alarms, 581 correctly left alone.

## Does the judge agree with itself?

- fields judged more than once: 841
- fields where the verdict changed between passes: 0 (**0.0%**)

A flip rate of exactly zero usually means the judge ran greedily (no temperature set), in which case repeated passes are near-identical by construction and this number measures nothing. Set `--temperature` on a temperature-capable model to make it informative.
A judge that flips on its own re-run cannot be more reliable than that flip rate,
whatever its precision looks like on a single pass.

## Where the judge and the truth disagree

| doc | field | judge said | truth says | value |
| --- | --- | --- | --- | --- |
| `000-invoice-std` | `bill_to_name` | contradicted | correct | Contoso Retail Group |
| `000-invoice-std` | `line_items[0].quantity` | contradicted | correct | 1.0 |
| `000-invoice-std` | `line_items[0].unit_price` | not_in_document | correct | 120.0 |
| `000-invoice-std` | `line_items[0].amount` | not_in_document | correct | 120.0 |
| `000-invoice-std` | `line_items[1].quantity` | contradicted | correct | 5.0 |
| `000-invoice-std` | `line_items[1].unit_price` | not_in_document | correct | 1200.0 |
| `000-invoice-std` | `line_items[1].amount` | contradicted | correct | 6000.0 |
| `000-invoice-std` | `tax_amount` | contradicted | correct | 0.0 |
| `001-invoice-std` | `issue_date` | not_in_document | correct | 2025-03-09 |
| `001-invoice-std` | `line_items[0].quantity` | not_in_document | correct | 2.0 |
| `001-invoice-std` | `line_items[0].unit_price` | contradicted | correct | 1200.0 |
| `001-invoice-std` | `line_items[0].amount` | not_in_document | correct | 2400.0 |
