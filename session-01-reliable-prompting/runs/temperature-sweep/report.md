# temperature 0 vs 0.7

- provider: `mock` · model: `mock-1`
- documents: 50 · repetitions per setting: 5
- prompt: `careful` · output mode: `strict`

`field agreement` is the share of fields on which every repetition of the same document
produced the same value. `identical outputs` is the share of documents where all
repetitions serialised to the same object — the strict version of the same question.

| temperature | field agreement | identical outputs | accuracy | hallucination rate | valid first try |
| --- | --- | --- | --- | --- | --- |
| 0.0 | 100.0% | 100.0% | 84.5% | 22.9% | 98.0% |
| 0.7 | 95.9% | 32.0% | 84.4% | 23.5% | 78.0% |

## Fields that moved most between repetitions

- **t=0.0**: none
- **t=0.7**: `purchase_order` ×9, `due_date` ×7, `years_experience` ×5, `total_amount` ×4, `location` ×4, `vendor_tax_id` ×3, `phone` ×1

## Documents whose answer changed between repetitions

A row with no unstable fields changed only in *serialisation* — casing, trailing
punctuation, key order. The values compare equal, but a byte-for-byte consumer
(a hash, a cache key, a diff in review) still sees a different answer every run.

| temperature | doc | distinct outputs | unstable fields |
| --- | --- | --- | --- |
| 0.7 | `029-resume-std` | 5 | _formatting only_ |
| 0.7 | `032-invoice-illegible` | 5 | due_date, purchase_order, total_amount, vendor_tax_id |
| 0.7 | `010-invoice-std` | 4 | purchase_order, total_amount |
| 0.7 | `018-resume-std` | 4 | location |
| 0.7 | `019-resume-std` | 4 | years_experience |
| 0.7 | `020-resume-std` | 4 | location |
| 0.7 | `022-resume-std` | 4 | _formatting only_ |
| 0.7 | `025-resume-std` | 4 | _formatting only_ |
| 0.7 | `026-resume-std` | 4 | _formatting only_ |
| 0.7 | `030-resume-std` | 4 | _formatting only_ |
| 0.7 | `034-invoice-credit_note` | 4 | due_date |
| 0.7 | `040-invoice-wrong_kind` | 4 | due_date, purchase_order, total_amount, vendor_tax_id |
