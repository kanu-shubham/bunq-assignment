# Prompt / output-mode comparison

| arm | valid 1st try | accuracy | hallucination rate | injection followed | refusals |
| --- | --- | --- | --- | --- | --- |
| `naive-freeform` | 90.0% | 75.6% | 31.0% | 1/3 | 1 |
| `careful-freeform` | 94.0% | 79.6% | 19.5% | 0/3 | 0 |
| `careful-freeform-repair` | 94.0% | 84.4% | 23.5% | 0/3 | 0 |
| `careful-strict-repair` | 98.0% | 84.5% | 22.9% | 0/3 | 0 |
