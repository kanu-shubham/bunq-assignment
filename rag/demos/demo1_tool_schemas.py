#!/usr/bin/env python3
"""Demo 1 — Tool use and tool schema design.

Shows the difference a schema makes, by linting a deliberately bad tool against
the real one, then demonstrating that strict validation catches the argument
errors you would otherwise discover in production.

    python demos/demo1_tool_schemas.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import PipelineConfig, RagPipeline, load_corpus  # noqa: E402
from ragkit.tools import (  # noqa: E402
    ToolInputError,
    ToolRegistry,
    ToolSpec,
    lint_tool,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# A tool definition of the kind that ships in a first draft. Every problem here
# is one the model pays for at runtime, not one the type checker catches.
BAD_TOOL = ToolSpec(
    name="Search",
    description="Searches the docs.",
    input_schema={
        "type": "object",
        "properties": {
            "q": {"type": "string"},
            "n": {"type": "integer"},
            "doc_type": {"type": "string"},
        },
    },
    handler=lambda q, n=5, doc_type=None: [],
    strict=True,
)


def main() -> int:
    rule("A tool definition before review")
    print(json.dumps(BAD_TOOL.to_anthropic(), indent=2)[:400])
    print("\nLint findings:")
    for finding in lint_tool(BAD_TOOL):
        print(f"  [{finding.severity:<7}] {finding.rule:<20} {finding.message}")

    print(
        "\nEvery one of those costs something concrete. An undescribed `n` gets a\n"
        "guessed value. A free-text `doc_type` gets 'Runbooks' and matches nothing.\n"
        "No trigger condition means the model calls it for questions it could have\n"
        "answered directly, and no boundary means it calls it again on the next turn."
    )

    rule("The same surface, after review")
    docs = load_corpus(ROOT / "data" / "corpus")
    pipeline = RagPipeline.build(docs, PipelineConfig(top_k=3))
    registry = pipeline.build_tool_registry()

    findings = registry.lint()
    print(f"{len(registry)} tools registered, {len(findings)} lint findings:")
    for finding in findings:
        print(f"  [{finding.severity:<7}] {finding.tool}: {finding.message}")
    if not findings:
        print("  (clean)")

    print("\nWire format handed to the Messages API `tools` array:\n")
    wire = registry.to_anthropic_tools()[0]
    print(json.dumps(wire, indent=2))

    rule("Strict validation catches bad arguments before the handler runs")
    cases = [
        ("valid", {"query": "how do I roll back a deploy", "top_k": 3}),
        ("unknown property", {"query": "x", "limit": 3}),
        ("wrong type", {"query": "x", "top_k": "three"}),
        ("out of range", {"query": "x", "top_k": 99}),
        ("bad enum value", {"query": "x", "doc_type": "Runbooks"}),
        ("missing required", {"top_k": 3}),
    ]
    search = registry.get("search_docs")
    for label, args in cases:
        try:
            search.validate(args)
            print(f"  {label:<18} OK")
        except ToolInputError as exc:
            print(f"  {label:<18} rejected: {exc}")

    print(
        "\nThe rejection message is written to be handed straight back to the model\n"
        "as a tool_result with is_error set. A model that is told 'top_k: 99 is above\n"
        "maximum 20' fixes its own call; a model that is told 'invalid request' guesses."
    )

    rule("Calling a tool for real")
    results = search.call({"query": "what happens when a retry keeps failing", "top_k": 3})
    for row in results:
        print(f"  {row['score']:.3f}  {row['doc_id']:<44} {row['heading'][:44]}")

    print("\nAnd a filter the model may not have needed:")
    results = search.call({"query": "duplicate submission", "top_k": 3, "doc_type": "postmortem"})
    for row in results:
        print(f"  {row['score']:.3f}  {row['doc_id']:<44} {row['doc_type']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
