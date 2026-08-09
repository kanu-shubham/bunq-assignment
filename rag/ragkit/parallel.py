"""Parallel tool execution.

Claude emits multiple ``tool_use`` blocks in a single assistant turn when the
calls are independent. Three things go wrong in almost every first
implementation, and all three are silent:

1. **The calls are executed serially.** The model went to the trouble of
   parallelising and the harness threw the advantage away. Three 400ms lookups
   should cost 400ms, not 1.2s.
2. **The results are returned in several user messages.** The API accepts it,
   nothing errors, and the model quietly stops emitting parallel calls in later
   turns because the transcript it is learning from no longer looks parallel.
   **All ``tool_result`` blocks for one assistant turn go in exactly one user
   message.**
3. **A failed call is dropped.** Every ``tool_use`` block needs a matching
   ``tool_result``; a failure is reported with ``is_error: true`` and a message
   the model can act on, not omitted. Omitting it is a malformed transcript.

Beyond that: not every tool is safe to run concurrently. A tool that writes
state is serialised here rather than trusted to be reentrant, and each call gets
its own timeout so one slow dependency cannot stall the turn.
"""

from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .tools import ToolExecutionError, ToolInputError, ToolRegistry


@dataclass(frozen=True)
class ToolCall:
    """One ``tool_use`` block from the model."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_block(cls, block: Any) -> "ToolCall":
        """Build from an SDK content block or an equivalent mapping."""
        if isinstance(block, Mapping):
            return cls(str(block["id"]), str(block["name"]), dict(block.get("input", {})))
        return cls(str(block.id), str(block.name), dict(block.input or {}))


@dataclass
class ToolResult:
    call: ToolCall
    content: Any
    is_error: bool = False
    duration_ms: float = 0.0
    attempts: int = 1

    def to_anthropic(self) -> dict[str, Any]:
        text = self.content if isinstance(self.content, str) else json.dumps(self.content, default=str)
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": self.call.id,
            "content": text,
        }
        if self.is_error:
            block["is_error"] = True
        return block


def extract_tool_calls(content_blocks: Sequence[Any]) -> list[ToolCall]:
    """Pull every ``tool_use`` block out of an assistant turn, in order."""
    calls: list[ToolCall] = []
    for block in content_blocks:
        kind = block.get("type") if isinstance(block, Mapping) else getattr(block, "type", None)
        if kind == "tool_use":
            calls.append(ToolCall.from_block(block))
    return calls


def _run_one(
    registry: ToolRegistry,
    call: ToolCall,
    *,
    max_retries: int | None,
    base_backoff: float,
    rng: random.Random,
) -> ToolResult:
    started = time.perf_counter()
    try:
        spec = registry.get(call.name)
    except ToolInputError as exc:
        return ToolResult(call, f"Tool error: {exc}", True, (time.perf_counter() - started) * 1000)

    retries = spec.max_retries if max_retries is None else max_retries
    last_error: str = "unknown error"
    for attempt in range(1, retries + 2):
        try:
            value = spec.call(call.arguments)
            return ToolResult(call, value, False, (time.perf_counter() - started) * 1000, attempt)
        except ToolInputError as exc:
            # Bad arguments never become good on a retry. Hand the model the
            # validation message so it can fix the call itself — that round trip
            # is cheaper and more reliable than guessing on its behalf.
            return ToolResult(
                call,
                f"Invalid arguments for {call.name}: {exc}",
                True,
                (time.perf_counter() - started) * 1000,
                attempt,
            )
        except ToolExecutionError as exc:
            last_error = str(exc)
            if not exc.retryable or attempt > retries:
                break
        except Exception as exc:  # noqa: BLE001 - a tool must never crash the turn
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt > retries:
                break
        # Exponential backoff with full jitter. Unjittered retries across
        # concurrent calls synchronise into exactly the herd they are meant to
        # avoid — the same failure as the webhook postmortem in the corpus.
        time.sleep(rng.uniform(0, base_backoff * (2 ** (attempt - 1))))

    return ToolResult(
        call,
        f"{call.name} failed: {last_error}",
        True,
        (time.perf_counter() - started) * 1000,
        retries + 1,
    )


@dataclass
class ParallelExecutionReport:
    results: list[ToolResult]
    wall_ms: float
    serial_ms: float
    parallel_calls: int
    serialised_calls: int

    @property
    def speedup(self) -> float:
        return self.serial_ms / self.wall_ms if self.wall_ms > 0 else 1.0

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.is_error)

    def to_anthropic_user_message(self) -> dict[str, Any]:
        """**One** user message containing **every** tool_result, in call order."""
        return {"role": "user", "content": [r.to_anthropic() for r in self.results]}


def execute_tool_calls(
    registry: ToolRegistry,
    calls: Sequence[ToolCall],
    *,
    max_workers: int = 8,
    default_timeout: float | None = None,
    max_retries: int | None = None,
    base_backoff: float = 0.05,
    seed: int | None = None,
) -> ParallelExecutionReport:
    """Run tool calls concurrently where safe, serially where not.

    Results are returned in the *same order as the calls*, regardless of
    completion order. Ordering is not cosmetic: the model matches results to
    calls by ``tool_use_id``, but a stable order keeps transcripts diffable and
    makes a flaky tool obvious in a log.
    """
    if not calls:
        return ParallelExecutionReport([], 0.0, 0.0, 0, 0)

    rng = random.Random(seed)
    parallel_idx: list[int] = []
    serial_idx: list[int] = []
    for i, call in enumerate(calls):
        try:
            safe = registry.get(call.name).parallel_safe
        except ToolInputError:
            safe = True  # unknown tool fails fast; no reason to serialise it
        (parallel_idx if safe else serial_idx).append(i)

    results: list[ToolResult | None] = [None] * len(calls)
    started = time.perf_counter()

    if parallel_idx:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(parallel_idx))) as pool:
            futures = {
                pool.submit(
                    _run_one,
                    registry,
                    calls[i],
                    max_retries=max_retries,
                    base_backoff=base_backoff,
                    rng=random.Random(rng.random()),
                ): i
                for i in parallel_idx
            }
            for future, i in futures.items():
                call = calls[i]
                try:
                    timeout = default_timeout
                    if timeout is None and call.name in registry:
                        timeout = registry.get(call.name).timeout_seconds
                    results[i] = future.result(timeout=timeout)
                except FuturesTimeout:
                    results[i] = ToolResult(
                        call,
                        f"{call.name} timed out after {timeout}s. "
                        "Retry with a narrower query or continue without this result.",
                        True,
                        (timeout or 0) * 1000,
                    )

    for i in serial_idx:
        results[i] = _run_one(
            registry, calls[i], max_retries=max_retries, base_backoff=base_backoff, rng=rng
        )

    wall_ms = (time.perf_counter() - started) * 1000
    final = [r for r in results if r is not None]
    return ParallelExecutionReport(
        results=final,
        wall_ms=wall_ms,
        serial_ms=sum(r.duration_ms for r in final),
        parallel_calls=len(parallel_idx),
        serialised_calls=len(serial_idx),
    )
