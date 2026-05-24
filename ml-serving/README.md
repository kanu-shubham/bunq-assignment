# ml-serving

A toy ML serving project. The model isn't the point — the serving is.

A scikit-learn logistic-regression classifier (Iris) wrapped in FastAPI,
containerised with Docker, and hammered with Locust so latency degradation
under load is visible with your own eyes.

## Layout

```
ml-serving/
├── train.py         # fit LogisticRegression on Iris, dump model.joblib
├── app.py           # FastAPI: /predict, /predict/batch, /healthz
├── locustfile.py    # load test: mix of single + batch requests
├── Dockerfile       # python:3.11-slim, trains at build time
├── requirements.txt
└── README.md
```

## Run locally

```bash
pip install -r requirements.txt
python train.py                                  # writes model.joblib
uvicorn app:app --host 0.0.0.0 --port 8000      # serve
```

Smoke test:

```bash
curl -s http://localhost:8000/healthz
curl -s -X POST http://localhost:8000/predict \
  -H 'content-type: application/json' \
  -d '{"sepal_length":5.1,"sepal_width":3.5,"petal_length":1.4,"petal_width":0.2}'
# -> {"label":"setosa","class_id":0,"proba":[0.98, 0.02, ~0]}
```

## Run in Docker

```bash
docker build -t iris-serving:0.1 .
docker run --rm -p 8000:8000 iris-serving:0.1
```

The Dockerfile runs `python train.py` at build time so the image is
self-contained — no model artefact needs to be mounted or downloaded at
startup.

## Load test

```bash
locust -f locustfile.py --host http://localhost:8000          # interactive UI
locust -f locustfile.py --host http://localhost:8000 \
  --headless -u 500 -r 200 -t 30s                              # headless
```

### Measured run (single uvicorn worker, local loopback)

| concurrent users | req/s | p50    | p95   | p99    | max    |
|-----------------:|------:|-------:|------:|-------:|-------:|
|              50  |   830 |  31 ms |  54 ms| 110 ms | 200 ms |
|             500  |   685 | 680 ms | 780 ms|1900 ms |2700 ms |

The interesting bit: a 10× jump in concurrency made p50 ~22× slower and p99
~17× slower, while throughput actually *dropped* (830 → 685 rps). Past the
knee, more clients = more queueing = worse latency for everyone, with no
throughput gained. That's the classic shape of a saturated single-threaded
server. The usual first move is `--workers N` (one process per CPU), but
that only buys CPU parallelism; the same curve repeats once those workers
saturate too.

## The "serious tools" — what they actually solve

You don't need them for an Iris classifier, but it's worth knowing the
shape of the problem each one is built around:

- **TorchServe** — PyTorch's official server. Multi-model loading,
  versioning, built-in metrics, request batching for GPU efficiency. Pick
  it when you ship PyTorch models and want batteries-included infra
  without writing your own.
- **NVIDIA Triton** — multi-framework (PyTorch, TF, ONNX, TensorRT),
  dynamic batching across in-flight requests, ensemble pipelines, runs on
  CPU or GPU. The serious choice when you need every drop of GPU
  throughput.
- **BentoML** — Python-first packaging: write `@bentoml.service`
  decorators, get a container, an OpenAPI spec, and adapters for the
  above runtimes. Sits one layer above Triton/TorchServe.
- **Ray Serve** — Python actors with replicas, request routing, model
  composition (pipeline A → B → C across machines), autoscaling. Pick it
  when serving *is* a distributed system, not a single endpoint.

## The four latency tricks

1. **Batching.** A model call has fixed per-request overhead (Python →
   C → GPU dispatch). Collecting N requests into one tensor amortises
   that overhead — throughput-per-GPU goes up, average latency stays
   roughly flat, tail latency improves. Triton/TorchServe do "dynamic
   batching" by waiting a few ms to coalesce in-flight requests. Our
   `/predict/batch` endpoint exposes the user-side version of this.
2. **Caching.** If inputs repeat (search queries, popular items, prompt
   prefixes for LLMs), memoise the result or the intermediate state.
   Cache hits drop to a hashmap lookup. Trade-off: invalidation and
   memory.
3. **Quantization.** Convert weights from fp32 → fp16 / int8 / int4. The
   model gets smaller (fits in less VRAM, loads faster) and arithmetic
   gets faster on hardware with int8 / fp16 paths. You pay a small
   accuracy hit; for most production models it's negligible after
   calibration.
4. **Async I/O.** If the request path does anything blocking — a DB call,
   an HTTP call to a feature store, reading a file — block the *event
   loop* and the whole worker stalls. `async def` + async clients let
   the worker handle other requests during the wait. FastAPI is async
   by default; our handlers are sync here because `predict_proba` is
   CPU-bound and there's nothing to await.

## What's deliberately missing

- No GPU. Iris is 4 floats; CPU is faster than the PCIe round-trip.
- No auth, no rate limit, no observability. That's the next layer.
- No model registry / versioning — `model.joblib` is baked into the
  image, which is the simplest thing that works and fine until it isn't.
- Single uvicorn worker in the Dockerfile. Bump `--workers` for real
  multi-core throughput; once that knee bends, the answer is one of the
  serving frameworks above, not more workers.
