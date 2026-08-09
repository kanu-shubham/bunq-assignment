#!/usr/bin/env python3
"""Demo 2 — Parallel tool calls.

Simulates one assistant turn containing four independent `tool_use` blocks and
executes them the way a correct harness does: concurrently, with per-call
timeouts, with failures reported rather than dropped, and with every result
returned in a single user message.

    python demos/demo2_parallel_tools.py
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import PipelineConfig, RagPipeline, load_corpus  # noqa: E402
from ragkit.parallel import ToolCall, execute_tool_calls, extract_tool_calls  # noqa: E402
from ragkit.tools import ToolExecutionError, ToolSpec  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# Stand-ins for the network-bound tools a real agent has, so the concurrency is
# visible. Each sleeps; none of them is doing real work.
def slow_lookup(entity: str) -> str:
    time.sleep(0.30)
    return f"looked up {entity}"


_attempts = {"n": 0}


def flaky_vendor(endpoint: str) -> str:
    """Fails twice, then succeeds — exercises the retry path."""
    _attempts["n"] += 1
    if _attempts["n"] < 3:
        raise ToolExecutionError(f"vendor {endpoint} returned 503", retryable=True)
    return f"vendor {endpoint} ok after {_attempts['n']} attempts"


def always_fails(reason: str) -> str:
    raise ToolExecutionError(f"permanent failure: {reason}", retryable=False)


def audit_write(event: str) -> str:
    """Side-effecting, therefore not parallel-safe."""
    time.sleep(0.10)
    return f"wrote audit event {event}"


def main() -> int:
    docs = load_corpus(ROOT / "data" / "corpus")
    pipeline = RagPipeline.build(docs, PipelineConfig(top_k=3))
    registry = pipeline.build_tool_registry()

    registry.register(ToolSpec(
        name="slow_lookup", description="stand-in for a slow network lookup",
        input_schema={"type": "object", "properties": {"entity": {"type": "string"}},
                      "required": ["entity"], "additionalProperties": False},
        handler=slow_lookup, timeout_seconds=2.0, strict=True,
    ))
    registry.register(ToolSpec(
        name="flaky_vendor", description="stand-in for a vendor that fails intermittently",
        input_schema={"type": "object", "properties": {"endpoint": {"type": "string"}},
                      "required": ["endpoint"], "additionalProperties": False},
        handler=flaky_vendor, max_retries=3, timeout_seconds=2.0, strict=True,
    ))
    registry.register(ToolSpec(
        name="always_fails", description="stand-in for a dependency that is simply down",
        input_schema={"type": "object", "properties": {"reason": {"type": "string"}},
                      "required": ["reason"], "additionalProperties": False},
        handler=always_fails, max_retries=0, timeout_seconds=2.0, strict=True,
    ))
    registry.register(ToolSpec(
        name="audit_write", description="stand-in for a side-effecting write",
        input_schema={"type": "object", "properties": {"event": {"type": "string"}},
                      "required": ["event"], "additionalProperties": False},
        handler=audit_write, side_effecting=True, timeout_seconds=2.0, strict=True,
    ))

    rule("The assistant turn we are responding to")
    # The shape the SDK hands you: several tool_use blocks in one message.
    assistant_content = [
        {"type": "text", "text": "Let me check a few things."},
        {"type": "tool_use", "id": "toolu_01", "name": "search_docs",
         "input": {"query": "webhook retry backoff", "top_k": 2}},
        {"type": "tool_use", "id": "toolu_02", "name": "slow_lookup", "input": {"entity": "partner-9931"}},
        {"type": "tool_use", "id": "toolu_03", "name": "slow_lookup", "input": {"entity": "partner-4412"}},
        {"type": "tool_use", "id": "toolu_04", "name": "flaky_vendor", "input": {"endpoint": "/status"}},
        {"type": "tool_use", "id": "toolu_05", "name": "always_fails", "input": {"reason": "database down"}},
        {"type": "tool_use", "id": "toolu_06", "name": "audit_write", "input": {"event": "lookup"}},
        {"type": "tool_use", "id": "toolu_07", "name": "search_docs", "input": {"query": "nonexistent", "top_k": 99}},
    ]
    calls = extract_tool_calls(assistant_content)
    for call in calls:
        spec_safe = registry.get(call.name).parallel_safe if call.name in registry else True
        print(f"  {call.id}  {call.name:<14} parallel_safe={spec_safe}")

    rule("Executing")
    report = execute_tool_calls(registry, calls, max_workers=8, seed=1)
    print(f"  wall clock          {report.wall_ms:7.0f} ms")
    print(f"  sum of durations    {report.serial_ms:7.0f} ms   (what serial execution would have cost)")
    print(f"  effective speedup   {report.speedup:7.2f}x")
    print(f"  parallel / serial   {report.parallel_calls} / {report.serialised_calls}")
    print(f"  errors              {report.errors}")

    print("\n  per call:")
    for result in report.results:
        status = "ERROR" if result.is_error else "ok"
        preview = str(result.content)[:58].replace("\n", " ")
        print(f"    {result.call.id}  {result.call.name:<14} {status:<5} "
              f"{result.duration_ms:6.0f}ms  attempts={result.attempts}  {preview}")

    rule("The user message returned to the model")
    message = report.to_anthropic_user_message()
    print(f"  role    : {message['role']}")
    print(f"  blocks  : {len(message['content'])}  (one per tool_use — none dropped)")
    print()
    for block in message["content"]:
        flag = "  is_error: true" if block.get("is_error") else ""
        print(f"    tool_use_id={block['tool_use_id']}{flag}")
        print(f"      {str(block['content'])[:100]}")

    print(
        "\nThe two things that matter and are easy to get wrong:\n"
        "  1. This is ONE user message. Splitting the results across several is\n"
        "     accepted by the API and teaches the model to stop calling tools in\n"
        "     parallel, which is a silent regression nobody attributes correctly.\n"
        "  2. Every tool_use has a tool_result, including the two that failed. A\n"
        "     dropped result is a malformed transcript; an is_error result with a\n"
        "     usable message lets the model recover on its own."
    )

    rule("Serial comparison, same work")
    _attempts["n"] = 0
    started = time.perf_counter()
    for call in calls:
        execute_tool_calls(registry, [call], seed=1)
    serial_ms = (time.perf_counter() - started) * 1000
    print(f"  one call at a time  {serial_ms:7.0f} ms")
    print(f"  all at once         {report.wall_ms:7.0f} ms")
    print(f"  saved               {serial_ms - report.wall_ms:7.0f} ms on a single turn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
