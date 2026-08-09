---
title: fraud-scoring
source: readme
space: engineering
team: risk
doc_type: readme
updated: 2026-05-27
tags: [fraud, risk, scoring, model, latency]
---

# fraud-scoring

`fraud-scoring` returns a risk score between 0 and 1000 for a proposed payment.
payments-api calls it synchronously before accepting an instruction, with a hard
budget of 120 milliseconds. If the budget is exceeded the caller applies the
configured fail-open score of 250 and tags the payment `scoring_degraded: true`.

## Model serving

The current production model is `gbm-v14`, a gradient boosted tree exported to
ONNX and served in-process. Model artefacts are pulled from the model registry
bucket at boot and pinned by digest — we never resolve `latest` at runtime.
Rolling out a new model is a config change to `model.pinned_digest` plus a
canary at 5% of traffic for 24 hours.

## Features

Feature computation is split between real-time features (amount, hour of day,
device fingerprint age, beneficiary novelty) and precomputed aggregates loaded
from the feature store every 15 minutes. Aggregate staleness is exported as
`fraud_feature_staleness_seconds`; the alert threshold is 1800.

## Score bands

    0-299     accept
    300-699   step-up authentication required
    700-1000  block and open a case

Bands are defined once in `bands.yaml` and shared with the case management tool
so that analysts and the service never disagree about what a score means.
