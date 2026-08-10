# Prototypes and Demonstrations: 6

Six small, runnable programs — one per pattern in [`../ai-agent-patterns.md`](../ai-agent-patterns.md).
Each is a single file you can read in five minutes and talk through in three.

No API key, no network, no dependencies (except the optional FastAPI layer in #6).
The model is stubbed by `ScriptedModel` in `llm.py`, whose block shapes (`text`,
`tool_use`, `tool_result`) mirror the Anthropic Messages API exactly — so the loop
you read is the loop you would ship. Swapping in `AnthropicModel` is one line:

```python
model = ScriptedModel(policy)     # deterministic, offline
model = AnthropicModel()          # pip install anthropic; ANTHROPIC_API_KEY=...
```

## Run them

```bash
cd docs/agent-patterns
python3 llm.py                      # smoke test of the model shim
python3 p1_observe_think_act.py     # incident agent: observe -> think -> act until healthy
python3 p2_react.py                 # ReAct: thought/action/observation, incl. tool error recovery
python3 p3_plan_execute.py          # plan, execute, fail, replan the remainder
python3 p4_ralph_loop.py            # stateless agent + stateful workspace, looped to green
python3 p5_checkpoint_resume.py     # crash mid-run; resume without double-charging
python3 p6_human_in_the_loop.py     # approval gate: one approved, one denied

pip install fastapi uvicorn
python3 p6_human_in_the_loop.py --serve   # same engine, HTTP approval queue (p6_api.py)
```

## What each one is meant to prove

| # | File | The claim it demonstrates | The thing to point at |
|---|------|---------------------------|------------------------|
| 1 | `p1_observe_think_act.py` | An agent in a changing world must re-observe, not remember | Fresh `Observation` per tick; the "rollback already in flight" branch |
| 2 | `p2_react.py` | Interleaved reasoning and acting recovers from bad tool results | `is_error=True` result → reformulated query on the next turn |
| 3 | `p3_plan_execute.py` | An explicit plan makes the run inspectable and cheap | 5 executed steps, 2 model calls; replan keeps completed work |
| 4 | `p4_ralph_loop.py` | Fresh context each iteration + filesystem memory converges | `fix_plan.md` diff across iterations; the four guardrails in `ralph()` |
| 5 | `p5_checkpoint_resume.py` | Crash-safety is journal + idempotency key, not retries | One charge per order across five attempts, incl. a mid-step kill |
| 6 | `p6_human_in_the_loop.py` | The gate belongs in the harness, not the prompt | `risk_of()` runs before any tool; denial returns as a tool result |

## Suggested demo order (≈12 minutes)

1. **#2 ReAct** — establishes the loop everyone recognises (2 min).
2. **#3 Plan-and-Execute** — contrast: same job, fewer model calls, inspectable artifact (2 min).
3. **#5 Checkpoint/Resume** — the production-readiness question (3 min).
4. **#6 HITL** — the safety question, with the FastAPI queue if there's time (3 min).
5. **#4 Ralph** — the "how do you do overnight work" curveball (2 min).

Prototype #1 is the base case; use it only if the interviewer starts from first
principles or asks about agents that watch systems rather than answer questions.

## Honest limitations (say these before they ask)

- **The model is scripted.** These demos prove the *harness* is correct, not that
  a model behaves. With a real model you would add: retries on 429/5xx, token
  accounting, context compaction, and an eval suite — see the guide's §9.
- **State is in-process** in #3 and #6 (`STORE`, `STATE`). #5 shows the shape the
  others would use in production: an append-only table keyed by run id.
- **`p2_react.py` uses `eval()`** for the calculator behind a character
  allow-list. Fine for a demo, not for untrusted input — a real tool would parse
  an AST or use a sandboxed expression evaluator.
- **No concurrency.** Parallel tool calls are executed sequentially in #2; the
  code returns all results in one message, which is the part that matters for the
  API contract.
