# Prototype 2 — chain-of-thought vs direct answer

- provider: `mock` · model: `mock-1` · thinking: `adaptive` · effort: `medium`
- task: does the printed total equal subtotal + tax? · 32 invoices

`false OK` is the expensive error — the audit passed an invoice whose arithmetic
does not add up. `invented verdict` is a true/false answer on a document that does
not print all three numbers, where the honest answer is null.

| arm | accuracy | false OK | false alarm | invented verdict | valid 1st try |
| --- | --- | --- | --- | --- | --- |
| `direct` | 53.1% | 1 | 8 | 2 | 100.0% |
| `cot` | 75.0% | 0 | 2 | 2 | 100.0% |
| `cot_fewshot` | 81.2% | 0 | 1 | 1 | 100.0% |

## Accuracy by difficulty

| arm | missing | obvious | reconciles | subtle |
| --- | --- | --- | --- | --- |
| `direct` | 60% (n=5) | 100% (n=2) | 52% (n=23) | 0% (n=2) |
| `cot` | 60% (n=5) | 100% (n=2) | 78% (n=23) | 50% (n=2) |
| `cot_fewshot` | 80% (n=5) | 100% (n=2) | 83% (n=23) | 50% (n=2) |

## Sample mistakes

- **direct**: `003-invoice-std` (reconciles: expected True, got False), `005-invoice-std` (reconciles: expected True, got False), `006-invoice-std` (reconciles: expected True, got None), `007-invoice-std` (reconciles: expected True, got False)
- **cot**: `003-invoice-std` (reconciles: expected True, got False), `006-invoice-std` (reconciles: expected True, got None), `029-invoice-illegible` (missing: expected None, got True), `037-invoice-german` (reconciles: expected True, got None)
- **cot_fewshot**: `006-invoice-std` (reconciles: expected True, got None), `029-invoice-illegible` (missing: expected None, got True), `037-invoice-german` (reconciles: expected True, got None), `038-invoice-mixed_currency` (reconciles: expected True, got False)
