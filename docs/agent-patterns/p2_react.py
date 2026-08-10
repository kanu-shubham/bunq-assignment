"""Prototype 2 — ReAct (Reason + Act).

Same loop as prototype 1, but the "world" is a set of tools and the agent's own
reasoning is part of the transcript: Thought -> Action -> Observation -> Thought
-> ... The model decides at each step whether it has enough to answer or needs
another tool call. Nothing is planned up front.

Task: "How many more employees does ACME have than Globex, and what percentage
of ACME's headcount is that gap?" — needs two lookups and some arithmetic, and
the first search deliberately misses so you can see the model recover from a bad
observation instead of crashing.

    python3 p2_react.py

What to point at in an interview:
  * The loop condition is `stop_reason == "tool_use"`. There is no step counter
    driving progress, only a `max_steps` circuit breaker.
  * A failed tool returns a `tool_result` with `is_error=True`. Errors are
    observations, not exceptions — that is what makes recovery possible.
  * Every `tool_use` id gets exactly one `tool_result`, in one user message.
    Splitting them across messages (or dropping one) breaks the next turn.
"""

from __future__ import annotations

import json

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
# Tools
# --------------------------------------------------------------------------

CORPUS = {
    "acme corp": "ACME Corp is a manufacturer founded in 1952. Headcount: 48200 employees.",
    "globex": "Globex Corporation is a conglomerate founded in 1989. Headcount: 31500 employees.",
}

TOOL_SCHEMAS = [
    {
        "name": "search",
        "description": "Full-text search over the company profile corpus. Returns matching profiles.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression, e.g. '(48200-31500)/48200*100'.",
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
]


def search(query: str) -> str:
    # Keyed on the registered name, so a plausible-but-wrong query misses.
    hits = [v for k, v in CORPUS.items() if k in query.lower()]
    if not hits:
        raise LookupError(f"no profile matched {query!r}; try the company's registered name")
    return "\n".join(hits)


def calculator(expression: str) -> str:
    allowed = set("0123456789.+-*/() ")
    if not set(expression) <= allowed:
        raise ValueError("expression contains unsupported characters")
    return str(round(eval(expression, {"__builtins__": {}}), 2))  # noqa: S307 - demo only


TOOLS = {"search": search, "calculator": calculator}


# --------------------------------------------------------------------------
# The reasoning policy (stand-in for the model)
# --------------------------------------------------------------------------

SYSTEM = """Answer the user's question using the tools.
Think out loud before each call, one call at a time. When you have everything you
need, answer in plain text and stop calling tools."""


def policy(system: str, messages: list[Message]) -> Reply:
    seen = transcript_text(messages)

    if "CALL search" not in seen:
        return think_and_call(
            "I need headcounts for both companies. Start with ACME.",
            "search",
            query="ACME headcount",
        )
    if "no profile matched" in seen and seen.count("CALL search") == 1:
        return think_and_call(
            "That query missed. The corpus is keyed on company names, so search the name itself.",
            "search",
            query="ACME Corp",
        )
    if seen.count("CALL search") < 3:
        return think_and_call(
            "ACME is 48200. Now Globex.", "search", query="Globex"
        )
    if "CALL calculator" not in seen:
        return think_and_call(
            "Gap is 48200-31500. I need it as a percentage of ACME's headcount.",
            "calculator",
            expression="(48200-31500)/48200*100",
        )
    pct = seen.rsplit("RESULT ", 1)[1].strip()
    return say(
        f"ACME has 16,700 more employees than Globex (48,200 vs 31,500) — "
        f"about {pct}% of ACME's headcount."
    )


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def react(question: str, model, max_steps: int = 8) -> tuple[str, list[Message]]:
    messages: list[Message] = [user(question)]

    for step in range(max_steps):
        reply = model.respond(SYSTEM, messages, TOOL_SCHEMAS)
        messages.append(reply.message)  # verbatim: tool_use blocks must survive

        if reply.stop_reason != "tool_use":
            return reply.text, messages

        # Execute every call in the turn, then return all results in ONE user
        # message. Real models emit parallel calls; splitting the results
        # teaches them to stop doing that.
        results = []
        for tc in reply.tool_calls:
            try:
                out = TOOLS[tc["name"]](**tc["input"])
                results.append(tool_result_block(tc["id"], out))
            except Exception as exc:  # tool failure is data, not control flow
                results.append(tool_result_block(tc["id"], f"{type(exc).__name__}: {exc}", True))
        messages.append({"role": "user", "content": results})

    return "stopped: step budget exhausted", messages


def render(messages: list[Message]) -> None:
    for m in messages:
        for b in m["content"]:
            if b["type"] == "text" and m["role"] == "user":
                print(f"QUESTION    {b['text']}")
            elif b["type"] == "text":
                print(f"THOUGHT     {b['text']}")
            elif b["type"] == "tool_use":
                print(f"ACTION      {b['name']}({json.dumps(b['input'])})")
            elif b["type"] == "tool_result":
                tag = "OBSERVATION" if not b["is_error"] else "OBS (error)"
                print(f"{tag} {b['content']}")
    print()


if __name__ == "__main__":
    model = ScriptedModel(policy)
    answer, messages = react(
        "How many more employees does ACME have than Globex, and what percentage "
        "of ACME's headcount is that gap?",
        model,
    )
    render(messages[:-1])  # last message is the answer, printed below
    print(f"ANSWER      {answer}")
    print(f"\nmodel calls: {model.calls}  transcript messages: {len(messages)}")
