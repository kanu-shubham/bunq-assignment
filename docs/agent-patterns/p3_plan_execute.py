"""Prototype 3 — Plan and Execute (with replanning).

ReAct decides one step at a time; Plan-and-Execute commits to a whole plan up
front, executes it mechanically, and only calls the model again when reality
disagrees with the plan. The plan is a data structure you can print, diff,
approve, resume, or hand to a human — which is most of why the pattern exists.

Task: publish the Q3 revenue report. The planner produces four steps; step 2
fails because the extract contains malformed rows, and the replanner splices in
a quarantine step rather than restarting the run.

    python3 p3_plan_execute.py

What to point at in an interview:
  * Two model calls for the whole run (plan + replan). Execution itself is
    plain code, so the per-step cost is a tool call, not a token bill.
  * Replanning is scoped to the *remaining* steps. Completed steps keep their
    results; that is what makes the pattern composable with checkpointing.
  * `attempts_left` bounds the replan loop. Without it, a permanently failing
    step produces an infinite planner spiral — the classic failure of this
    pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llm import Message, Reply, ScriptedModel, say, transcript_text, user

# --------------------------------------------------------------------------
# Plan representation
# --------------------------------------------------------------------------


@dataclass
class Step:
    id: str
    description: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | done | failed
    result: str | None = None


@dataclass
class Plan:
    goal: str
    steps: list[Step]

    def pending(self) -> list[Step]:
        return [s for s in self.steps if s.status == "pending"]

    def render(self) -> str:
        mark = {"pending": "[ ]", "done": "[x]", "failed": "[!]"}
        return "\n".join(
            f"  {mark[s.status]} {s.id}: {s.description}"
            + (f"\n        -> {s.result}" if s.result else "")
            for s in self.steps
        )


# --------------------------------------------------------------------------
# Tools (the executor's vocabulary)
# --------------------------------------------------------------------------

STATE: dict[str, Any] = {"rows": 0, "bad_rows": 0, "quarantined": False, "published": False}


def extract(source: str) -> str:
    STATE["rows"], STATE["bad_rows"] = 12_000, 37
    return f"extracted 12000 rows from {source} (37 with unparseable currency fields)"


def validate(dataset: str) -> str:
    if STATE["bad_rows"] and not STATE["quarantined"]:
        raise ValueError(f"{STATE['bad_rows']} rows failed currency validation")
    return f"{dataset} passed validation ({STATE['rows']} rows)"


def quarantine(reason: str) -> str:
    STATE["quarantined"] = True
    STATE["rows"] -= STATE["bad_rows"]
    return f"moved {STATE['bad_rows']} rows to quarantine ({reason})"


def aggregate(by: str) -> str:
    return f"aggregated {STATE['rows']} rows by {by}"


def publish(target: str) -> str:
    if not STATE["quarantined"] and STATE["bad_rows"]:
        raise RuntimeError("refusing to publish an unvalidated dataset")
    STATE["published"] = True
    return f"published Q3 revenue report to {target}"


TOOLS = {
    "extract": extract,
    "validate": validate,
    "quarantine": quarantine,
    "aggregate": aggregate,
    "publish": publish,
}


# --------------------------------------------------------------------------
# Planner / replanner. One model, two prompts.
# --------------------------------------------------------------------------

PLANNER_SYSTEM = """You are a planner. Given a goal and the available tools,
emit a numbered plan of tool calls. Do not execute anything.
Tools: extract(source), validate(dataset), quarantine(reason), aggregate(by), publish(target)"""

REPLANNER_SYSTEM = """You are a replanner. You are given the original goal, the
plan so far with results, and the step that failed. Emit a new plan covering only
the work that still has to happen. Reuse completed results; do not redo them."""


def planner_policy(system: str, messages: list[Message]) -> Reply:
    """Stands in for a model emitting a structured plan."""
    if "FAILED" in transcript_text(messages):
        # Replan: the validation failure is data quality, so quarantine first.
        return say(
            "s3|quarantine|reason=unparseable currency fields\n"
            "s2b|validate|dataset=q3_sales\n"
            "s4|aggregate|by=region\n"
            "s5|publish|target=warehouse.reporting.q3_revenue"
        )
    return say(
        "s1|extract|source=sales_db\n"
        "s2|validate|dataset=q3_sales\n"
        "s4|aggregate|by=region\n"
        "s5|publish|target=warehouse.reporting.q3_revenue"
    )


DESCRIPTIONS = {
    "extract": "pull the raw Q3 sales extract",
    "validate": "validate the extract",
    "quarantine": "quarantine unparseable rows",
    "aggregate": "aggregate revenue by region",
    "publish": "publish to the warehouse",
}


def parse_plan(goal: str, text: str) -> Plan:
    """Parse the model's plan into steps. In production this is a tool call with
    a JSON schema (or structured output) rather than line parsing — but the
    validation boundary is the point: never execute unparsed model output."""
    steps = []
    for line in text.strip().splitlines():
        sid, tool, *rest = line.split("|")
        if tool not in TOOLS:
            raise ValueError(f"plan references unknown tool {tool!r}")
        args = dict(part.split("=", 1) for part in rest[0].split(",")) if rest else {}
        steps.append(Step(sid, DESCRIPTIONS[tool], tool, args))
    return Plan(goal, steps)


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def run(goal: str, model, attempts_left: int = 2) -> Plan:
    plan = parse_plan(goal, model.respond(PLANNER_SYSTEM, [user(goal)]).text)
    print(f"PLAN\n{plan.render()}\n")

    while True:
        failure = None
        for step in plan.pending():
            try:
                step.result = TOOLS[step.tool](**step.args)
                step.status = "done"
                print(f"EXEC  {step.id} {step.tool:<10} ok   {step.result}")
            except Exception as exc:
                step.status, step.result = "failed", f"{type(exc).__name__}: {exc}"
                failure = step
                print(f"EXEC  {step.id} {step.tool:<10} FAIL {step.result}")
                break  # stop the run; downstream steps assume this one worked

        if failure is None:
            return plan
        if attempts_left <= 0:
            print("\nout of replan attempts — escalating to a human")
            return plan

        attempts_left -= 1
        print(f"\nREPLAN (attempts left after this: {attempts_left})")
        context = user(
            f"goal: {goal}\nplan so far:\n{plan.render()}\n"
            f"FAILED at {failure.id}: {failure.result}"
        )
        revision = parse_plan(goal, model.respond(REPLANNER_SYSTEM, [context]).text)
        # Keep finished work, replace everything from the failure onwards.
        plan = Plan(goal, [s for s in plan.steps if s.status == "done"] + revision.steps)
        print(f"{plan.render()}\n")


if __name__ == "__main__":
    model = ScriptedModel(planner_policy)
    final = run("Publish the Q3 revenue report to the warehouse", model)
    print(f"\nFINAL PLAN\n{final.render()}")
    print(f"\nmodel calls: {model.calls}  published: {STATE['published']}")
