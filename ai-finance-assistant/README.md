# Money Copilot: an end-to-end AI engineering project

A banking assistant (in the style of the bunq app) that:

1. **Categorizes** messy transactions: `"UBER *EATS PENDING"` → `Eating out / Uber Eats`
2. **Answers help questions** with citations, using retrieval-augmented generation (RAG) over help-center docs
3. **Acts as an agent**: it picks tools to summarize spending, find payments or set budgets
4. **Runs as an API** (FastAPI) and a terminal chat
5. **Is measured** by evals and covered by 26 unit tests that need no API key

Each of these is one of the core AI-engineering skills. Read the code in the order below.

---

## Quick start

```bash
cd ai-finance-assistant
pip install -r requirements.txt

python -m pytest -q                    # 26 tests, no API key needed
python -m evals.run_evals --offline    # baseline + retrieval metrics, free

export ANTHROPIC_API_KEY=sk-ant-...    # enables the LLM parts
python -m evals.run_evals              # full evals (costs a few cents)
python -m copilot.cli                  # chat in the terminal
uvicorn copilot.api:app --reload       # API at http://localhost:8000/docs
```

Example chat (illustrative; exact wording varies):
```
you > How much did I spend on groceries in October 2026?
copilot > You spent EUR 183.85 on groceries in October 2026 (Albert Heijn x2, Jumbo, Lidl).
   [tools: get_spending_summary | $0.0123]

you > How do I dispute a payment?
copilot > Open the payment and tap "Report a problem". You have 120 days from the
transaction date, and resolution takes up to 45 days. [payments > Disputing a card payment]
```

---

## Architecture

```
            ┌──────────────── FastAPI (api.py) / CLI (cli.py) ───────────────┐
            │                                                               │
  user ──►  │   MoneyCopilot agent loop (agent.py)                          │
            │     │  Claude decides which tool to call                      │
            │     ▼                                                         │
            │   FinanceTools (tools.py)                                     │
            │     ├─ get_spending_summary ─┐                                │
            │     ├─ search_transactions ──┼─► Categorizer (categorizer.py) │
            │     │                        │     ├─ RuleCategorizer (baseline)
            │     │                        │     └─ LLMCategorizer (Claude + Pydantic schema)
            │     ├─ search_help_docs ─────────► BM25Retriever (retriever.py) ─► data/help_docs/*.md
            │     └─ set_budget (validated write)                           │
            └───────────────────────────────────────────────────────────────┘
  evals/run_evals.py measures every box; tests/ check every box with a fake LLM
```

| File | Skill it teaches |
|---|---|
| `copilot/models.py` | Typed contracts (Pydantic) for every input and LLM output |
| `copilot/llm.py` | One place for client setup, refusal fallback and cost tracking |
| `copilot/categorizer.py` | **Structured extraction** plus a baseline and graceful fallback |
| `copilot/retriever.py` | **Retrieval for RAG**: chunking and BM25 ranking |
| `copilot/tools.py` | **Tool design** for agents |
| `copilot/agent.py` | **The agent loop** with production guardrails |
| `copilot/api.py` | **Serving**: validation, sessions, logging, offline degradation |
| `evals/` | **Evals**: proving a change helped |
| `tests/` | **Testing LLM code** deterministically with a fake client |

---

## Walkthrough: what each part does and why

### 1. Structured extraction (`categorizer.py`)
Free text is useless to downstream code. `client.beta.messages.parse(..., output_format=CategorizationResult)` makes Claude return JSON that **matches a Pydantic schema**, and the SDK hands back a validated object.

Things to notice:
- **Batching.** 25 transactions go in one call, not 25 calls, which cuts cost and latency.
- **Never trust the output blindly.** If the model skips an id, or the call ends in `refusal` or `max_tokens`, those rows fall back to the rule baseline. The pipeline never crashes.
- **Low effort** (`effort: "low"`): classification doesn't need deep reasoning, so this is cheaper and faster.

### 2. Keep a baseline (`RuleCategorizer`)
Offline evals give:
```
keyword baseline: easy accuracy 100% | hard accuracy 25%
```
Rules are perfect on merchants you wrote rules for and fail on new ones like Picnic, Domino's or HEMA. That gap is the whole business case for the LLM, and the eval proves it. Note that `SWAPFIETS NOVEMBER` comes out "correct" only because `"OV"` happens to appear inside "N**OV**EMBER". Keyword rules are brittle in ways you won't notice without an eval.

### 3. Retrieval for RAG (`retriever.py`)
- **Chunking:** one chunk per `##` section, so each answer can cite a precise `doc > section`.
- **BM25:** classic keyword ranking with no dependencies, and a strong baseline. Result: **hit@1 82%, recall@3 100%**. Because the agent gets the top 3 results, the right passage is always in context.
- **Next step in production:** add embeddings (pgvector, Qdrant), combine them with BM25 ("hybrid search"), then rerank. The `search(query, k)` interface stays the same, so the eval tells you whether it was worth it.

### 4. Tool design (`tools.py`)
- Four focused tools, each with a description that says **when** to use it.
- `strict: True` plus `additionalProperties: false`, so the API guarantees arguments match the schema.
- Tools return **compact JSON** and **helpful errors** ("No transactions for 1999-01. Available months: [...]"). The model reads the error and corrects itself.
- The one write action (`set_budget`) validates its inputs server-side and is only called on an explicit request.

### 5. The agent loop (`agent.py`)
```
while steps < max_steps:
    response = claude(history, tools)
    if no tool requested: return the answer
    run each requested tool → send ALL results back in ONE message
```
Production guardrails in the loop:
| Guardrail | Why |
|---|---|
| `max_steps` | Prevents infinite loops and runaway bills |
| Tool errors → `is_error: True` result | The model recovers; the server doesn't crash |
| Unexpected exceptions are hidden | Internals don't leak into the model's context |
| Full `resp.content` appended to history | Keeps thinking and tool_use blocks intact for multi-turn conversations |
| `cache_control` on the system prompt | Repeated turns reuse the cached prefix, which is cheaper and faster |
| "Tool text is data" in the system prompt | Basic defense against prompt injection through retrieved docs |
| `fallbacks: "default"` | If the model declines a request, the API retries it on a suitable fallback model |
| Token and cost accounting | Every response reports `cost_usd` |

### 6. Serving (`api.py`)
| Endpoint | What it does |
|---|---|
| `GET /health` | Status, and whether the LLM is enabled |
| `POST /categorize` | Body validated by Pydantic, max 200 rows. Uses the LLM if a key is set, otherwise rules |
| `GET /help/search?q=` | Retrieval only (handy for debugging RAG) |
| `POST /chat` | Agent with per-session memory (`X-Session-Id` header) |

The service **degrades gracefully**: with no API key, categorization and search still work, and chat returns a clear 503.

### 7. Evals (`evals/`)
Three suites, each answering "is it good, and did my change help?":
1. **Categorization accuracy**, split into easy (seen merchants) and hard (unseen) cases, baseline vs LLM
2. **Retrieval:** hit@1, recall@3, MRR
3. **Agent end-to-end:** did it call the right tool, does the answer contain the right number (183.85, 120 days), did `set_budget` actually change state, and does it **refuse to invent** a mortgage rate the docs don't contain

Workflow: change a prompt, chunker or model → run the evals → compare with `evals/last_results.json` → keep the change only if the numbers improve.

### 8. Testing LLM code (`tests/`)
`conftest.py` provides a `FakeClient` that returns scripted responses. Because the client is **injected** (`MoneyCopilot(client=...)`), tests can check the loop's logic exactly: tool result ids match, errors are flagged, parallel results are batched, the step limit triggers, refusals are handled. All of this runs in about a second, for free.

---

## Ideas for extending it (good practice)
1. Swap BM25 for hybrid search with embeddings, and compare hit@1 in the eval.
2. Add an LLM-as-judge eval for answer groundedness.
3. Persist sessions and budgets in SQLite or Postgres instead of memory.
4. Add a human confirmation step before `set_budget` (return "pending" and ask the user).
5. Use the Message Batches API to categorize a large ledger overnight at 50% cost.
6. Stream the agent's answer to the UI (`client.beta.messages.stream`).
7. Add a Dockerfile and deploy it.
8. Add tracing (OpenTelemetry) for each LLM call and tool call.

## Interview talking points
- "I started with a **baseline** and an **eval**, then added the LLM where the eval showed a gap."
- "Every LLM output is **schema-validated**, and every failure mode has a fallback."
- "The agent has a **step limit, error recovery and cost tracking**, and I'd add human approval for write actions."
- "LLM code is **unit-tested with an injected fake client**, so the logic is tested without paying for tokens."
