# Prompt / output-mode comparison

| arm | valid 1st try | accuracy | hallucination rate | injection followed | refusals |
| --- | --- | --- | --- | --- | --- |
| `naive-freeform` | 96.0% | 80.8% | 40.7% | 0/3 | 1 |
| `careful-freeform` | 100.0% | 83.8% | 30.3% | 0/3 | 0 |
| `careful-freeform-repair` | 100.0% | 84.1% | 28.7% | 0/3 | 0 |
| `careful-strict-repair` | 100.0% | 84.1% | 28.7% | 0/3 | 0 |
