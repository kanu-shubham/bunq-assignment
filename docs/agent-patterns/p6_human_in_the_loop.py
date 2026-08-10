"""Prototype 6 — Human in the Loop.

An approval gate in front of irreversible actions. The interesting part is not
the prompt ("ask before refunding") — models are not a security boundary — but
the *harness*: a policy the agent cannot talk its way past, a run that suspends
when it hits the gate, and a resume path that turns a human decision back into a
tool result the model can react to.

Scenario: a support agent that can look up orders and issue refunds. Refunds up
to EUR 50 are automatic, above that a human approves, and account deletion is
never available to the agent at all.

    python3 p6_human_in_the_loop.py           # offline: approve one, deny one
    python3 p6_human_in_the_loop.py --serve   # same engine behind a FastAPI app

What to point at in an interview:
  * Risk tiering, not "ask about everything": approval fatigue makes reviewers
    rubber-stamp, which is worse than no gate.
  * The pause is durable state (`RunStore`), not a blocked thread. A human may
    take three days; the process should not be holding a socket open.
  * A denial is fed back as a tool_result, so the agent adapts instead of
    crashing — and the reviewer's reason becomes part of the transcript.
  * Approval is bound to the exact arguments that were shown. Re-planning after
    approval must not be able to change the amount.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from llm import (
    Message,
    Reply,
    ScriptedModel,
    say,
    think_and_call,
    tool_result_block,
    transcript_text,
    user,
)

# --------------------------------------------------------------------------
# Tools and the policy in front of them
# --------------------------------------------------------------------------

ORDERS = {"ord-1191": {"customer": "r.dijkstra", "total": 189.00, "status": "delivered"}}
REFUNDS: list[dict[str, Any]] = []

AUTO_APPROVE_LIMIT = 50.00


def lookup_order(order_id: str) -> str:
    o = ORDERS.get(order_id)
    return json.dumps(o) if o else f"no such order {order_id}"


def issue_refund(order_id: str, amount: float, reason: str) -> str:
    REFUNDS.append({"order_id": order_id, "amount": amount, "reason": reason})
    return f"refunded EUR {amount:.2f} on {order_id}"


TOOLS: dict[str, Callable[..., str]] = {
    "lookup_order": lookup_order,
    "issue_refund": issue_refund,
}


def risk_of(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Return (tier, rationale). Tiers: auto | confirm | forbid.

    This runs in the harness, before any tool executes. Prompt injection in a
    tool result cannot reach it, and neither can a persuasive model.
    """
    if name not in TOOLS:
        return "forbid", f"{name} is not an exposed capability"
    if name == "issue_refund":
        amount = float(args.get("amount", 0))
        if amount > AUTO_APPROVE_LIMIT:
            return "confirm", f"refund of EUR {amount:.2f} exceeds the EUR {AUTO_APPROVE_LIMIT:.0f} limit"
    return "auto", "read-only or below the approval threshold"


def approval_fingerprint(name: str, args: dict[str, Any]) -> str:
    """What the reviewer saw. Binding the decision to this hash stops an agent
    from getting approval for EUR 60 and then executing EUR 600."""
    blob = json.dumps({"tool": name, "args": args}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Durable run state
# --------------------------------------------------------------------------


@dataclass
class Run:
    id: str
    question: str
    messages: list[Message]
    status: str = "running"  # running | awaiting_approval | done | refused
    pending: dict[str, Any] | None = None
    answer: str | None = None
    audit: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "pending": self.pending,
            "answer": self.answer,
            "audit": self.audit,
        }


class RunStore(dict[str, Run]):
    """In-memory for the demo; a table with the same columns in production."""


STORE = RunStore()


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------

SYSTEM = """You are a customer support agent. Use lookup_order before refunding.
Refunds above EUR 50 need human approval; ask for the full amount when the
customer's complaint justifies it."""


def policy(system: str, messages: list[Message]) -> Reply:
    seen = transcript_text(messages)
    if "CALL lookup_order" not in seen:
        return think_and_call(
            "Check the order before promising anything.", "lookup_order", order_id="ord-1191"
        )
    if "CALL issue_refund" not in seen:
        return think_and_call(
            "Delivered but damaged; the customer is owed the full 189.00.",
            "issue_refund",
            order_id="ord-1191",
            amount=189.00,
            reason="item arrived damaged",
        )
    if "denied by reviewer" in seen:
        # The denial carried a reason, so the agent can offer the fallback the
        # reviewer suggested instead of simply failing.
        return say(
            "I can't authorise a full refund here. I've logged the damage report and "
            "a supervisor will call you within one business day about the EUR 189.00."
        )
    return say("Refund issued — you'll see EUR 189.00 back within three working days.")


def step(run: Run, model) -> Run:
    """Advance a run until it finishes or hits an approval gate."""
    while run.status == "running":
        reply = model.respond(SYSTEM, run.messages)
        run.messages.append(reply.message)

        if reply.stop_reason != "tool_use":
            run.status, run.answer = "done", reply.text
            return run

        results = []
        for tc in reply.tool_calls:
            tier, why = risk_of(tc["name"], tc["input"])

            if tier == "forbid":
                run.audit.append(f"FORBID {tc['name']}: {why}")
                results.append(tool_result_block(tc["id"], f"not permitted: {why}", True))
                continue

            if tier == "confirm":
                run.status = "awaiting_approval"
                run.pending = {
                    "tool_use_id": tc["id"],
                    "tool": tc["name"],
                    "args": tc["input"],
                    "why": why,
                    "fingerprint": approval_fingerprint(tc["name"], tc["input"]),
                }
                run.audit.append(f"PAUSE  {tc['name']}({tc['input']}): {why}")
                # Anything already executed in this turn still needs its result,
                # so park them with the pending approval.
                run.pending["partial_results"] = results
                return run

            out = TOOLS[tc["name"]](**tc["input"])
            run.audit.append(f"AUTO   {tc['name']} -> {out}")
            results.append(tool_result_block(tc["id"], out))

        run.messages.append({"role": "user", "content": results})
    return run


def decide(run: Run, approved: bool, reviewer: str, note: str, model) -> Run:
    """Apply a human decision and resume the run."""
    if run.status != "awaiting_approval" or not run.pending:
        raise ValueError(f"run {run.id} is not awaiting approval")

    p = run.pending
    if approval_fingerprint(p["tool"], p["args"]) != p["fingerprint"]:
        raise ValueError("arguments changed since approval was requested")

    results = list(p.get("partial_results", []))
    if approved:
        out = TOOLS[p["tool"]](**p["args"])
        run.audit.append(f"APPROVE {reviewer}: {p['tool']} -> {out}")
        results.append(tool_result_block(p["tool_use_id"], out))
    else:
        run.audit.append(f"DENY   {reviewer}: {note}")
        results.append(
            tool_result_block(p["tool_use_id"], f"denied by reviewer ({reviewer}): {note}", True)
        )

    run.pending = None
    run.status = "running"
    run.messages.append({"role": "user", "content": results})
    return step(run, model)


def start(question: str, model) -> Run:
    run = Run(id=f"run-{uuid.uuid4().hex[:6]}", question=question, messages=[user(question)])
    STORE[run.id] = run
    return step(run, model)


# --------------------------------------------------------------------------

QUESTION = "My order ord-1191 arrived damaged. I want my money back."


def demo() -> None:
    for approved, note in ((True, "photo evidence checked"), (False, "no photo on file; escalate")):
        REFUNDS.clear()
        run = start(QUESTION, ScriptedModel(policy))
        print(f"\n=== run {run.id}: status={run.status}")
        print(f"    pending: {json.dumps(run.pending, default=str)[:120]}...")

        run = decide(run, approved, "b.jansen", note, ScriptedModel(policy))
        print(f"    decision: {'APPROVED' if approved else 'DENIED'} ({note})")
        print(f"    status={run.status}")
        for line in run.audit:
            print(f"      {line}")
        print(f"    answer: {run.answer}")
        print(f"    refunds executed: {REFUNDS}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="run the FastAPI app instead")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if args.serve:
        import uvicorn  # the HTTP layer lives in p6_api.py; the engine above is transport-agnostic

        uvicorn.run("p6_api:app", host="127.0.0.1", port=args.port)
    else:
        demo()
