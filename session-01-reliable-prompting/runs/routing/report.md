# Prototype 3 — few-shot vs zero-shot (document routing)

- provider: `mock` · model: `mock-1`
- 50 documents into 5 queues

`invoice` is the majority class, so accuracy alone flatters a classifier that
collapses everything into it. The per-class recall table below is the one to read.

| k (examples) | prompt size | accuracy | recall invoice | recall credit_note | recall resume | recall delivery_note | recall unreadable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 541 chars | 88.0% | 85% | 0% | 100% | 100% | 100% |
| 1 | 659 chars | 92.0% | 89% | 50% | 100% | 100% | 100% |
| 2 | 803 chars | 96.0% | 96% | 50% | 100% | 100% | 100% |
| 4 | 1080 chars | 96.0% | 96% | 50% | 100% | 100% | 100% |
| 8 | 1619 chars | 96.0% | 96% | 50% | 100% | 100% | 100% |

Support (documents per class): invoice 27, credit_note 2, resume 18, delivery_note 1, unreadable 2. Recall on a class with two documents moves in 50-point steps — read it as a direction, not a measurement.

## What each class gets confused with

- **k=0**: invoice → delivery_note ×2, credit_note ×2; credit_note → invoice ×2
- **k=1**: invoice → credit_note ×2, delivery_note ×1; credit_note → invoice ×1
- **k=2**: invoice → credit_note ×1; credit_note → invoice ×1
- **k=4**: invoice → credit_note ×1; credit_note → invoice ×1
- **k=8**: invoice → credit_note ×1; credit_note → invoice ×1
