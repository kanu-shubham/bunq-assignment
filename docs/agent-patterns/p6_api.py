"""HTTP surface for prototype 6 — the approval queue as a FastAPI service.

Deliberately a separate module from the engine: the agent loop in
`p6_human_in_the_loop.py` knows nothing about HTTP, and this file contains no
agent logic. That split is the point of the pattern — a paused run is a row in a
store, so any transport (REST, Slack, an internal admin tool, a cron that
auto-denies after 24h) can supply the decision.

    pip install fastapi uvicorn
    python3 p6_human_in_the_loop.py --serve      # or: uvicorn p6_api:app

    curl -s localhost:8000/runs -H 'content-type: application/json' \
         -d '{"question":"My order ord-1191 arrived damaged. I want my money back."}'
    curl -s localhost:8000/approvals
    curl -s localhost:8000/runs/<id>/decision -H 'content-type: application/json' \
         -d '{"approved":true,"reviewer":"b.jansen","note":"photo checked"}'

Note there is no `from __future__ import annotations` here: FastAPI resolves the
handler type hints at import time, and postponed annotations plus locally
defined models is a reliable way to get a confusing 422.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from llm import ScriptedModel
from p6_human_in_the_loop import STORE, decide, policy, start


class StartBody(BaseModel):
    question: str


class DecisionBody(BaseModel):
    approved: bool
    reviewer: str
    note: str = ""


app = FastAPI(title="Agent approvals")


def model() -> ScriptedModel:
    """Swap for AnthropicModel() to drive the same endpoints with a real model."""
    return ScriptedModel(policy)


@app.post("/runs", status_code=201)
def create_run(body: StartBody):
    """Start a run. Returns immediately — possibly already parked on a gate."""
    return start(body.question, model()).public()


@app.get("/runs")
def list_runs():
    return [r.public() for r in STORE.values()]


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in STORE:
        raise HTTPException(404, "no such run")
    return STORE[run_id].public()


@app.get("/approvals")
def approval_queue():
    """The reviewer's inbox: every run suspended on an approval gate."""
    return [r.public() for r in STORE.values() if r.status == "awaiting_approval"]


@app.post("/runs/{run_id}/decision")
def post_decision(run_id: str, body: DecisionBody):
    """Apply a human decision and resume the run in the same request.

    409 on a run that is not awaiting approval — that covers the double-click
    and the two-reviewers-at-once race without any extra locking.
    """
    if run_id not in STORE:
        raise HTTPException(404, "no such run")
    try:
        run = decide(STORE[run_id], body.approved, body.reviewer, body.note, model())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return run.public()
