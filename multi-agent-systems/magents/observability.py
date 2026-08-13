"""Tracing for agent runs.

An agent system you cannot see is an agent system you cannot operate. The
minimum viable trace, and what each field is for when something goes wrong at
2am:

  trace_id / span_id / parent  — reconstruct the call tree across parallel agents
  attributes                   — model, effort, tool name, decision taken
  tokens + cost                — attribute spend to a *node*, not to a month
  status + error               — which specialist failed, not "the run failed"

Two things people leave out and regret:
  * **The prompt hash, not the prompt.** Full prompts in traces are a PII
    liability and blow up storage; a hash lets you group runs by prompt version
    and diff two versions when quality moves.
  * **Decision points.** Log why the router chose a branch. Most "why did the
    agent do that?" investigations are really "which branch did it take and on
    what evidence?".
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

# Claude Opus 5 list price, USD per token. Cache reads are ~0.1x input.
PRICE_PER_TOKEN = {
    "claude-opus-5": (5.0 / 1_000_000, 25.0 / 1_000_000),
    "claude-sonnet-5": (3.0 / 1_000_000, 15.0 / 1_000_000),
    "claude-haiku-4-5": (1.0 / 1_000_000, 5.0 / 1_000_000),
}


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: str | None = None
    started_at: float = field(default_factory=time.perf_counter)
    ended_at: float | None = None
    status: str = "ok"
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    model: str | None = None

    @property
    def duration_ms(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.perf_counter()
        return (end - self.started_at) * 1000

    @property
    def cost_usd(self) -> float:
        rate_in, rate_out = PRICE_PER_TOKEN.get(self.model or "", (0.0, 0.0))
        billed_input = max(0, self.input_tokens - self.cached_tokens)
        return billed_input * rate_in + self.cached_tokens * rate_in * 0.1 + self.output_tokens * rate_out


class Tracer:
    def __init__(self, trace_id: str | None = None):
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.spans: list[Span] = []
        self._stack: list[Span] = []

    @contextmanager
    def span(self, name: str, **attributes):
        span = Span(
            name=name,
            trace_id=self.trace_id,
            parent_id=self._stack[-1].span_id if self._stack else None,
            attributes=dict(attributes),
        )
        self.spans.append(span)
        self._stack.append(span)
        try:
            yield span
        except Exception as exc:
            span.status = "error"
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.ended_at = time.perf_counter()
            self._stack.pop()

    def record_usage(self, span: Span, usage, model: str) -> None:
        span.input_tokens += usage.input_tokens
        span.output_tokens += usage.output_tokens
        span.cached_tokens += getattr(usage, "cache_read_input_tokens", 0)
        span.model = model

    def decision(self, name: str, chosen: str, because: str, **context) -> None:
        """Zero-duration marker span. Makes routing auditable after the fact."""
        span = Span(
            name=f"decision:{name}",
            trace_id=self.trace_id,
            parent_id=self._stack[-1].span_id if self._stack else None,
            attributes={"chosen": chosen, "because": because, **context},
        )
        span.ended_at = span.started_at
        self.spans.append(span)

    # -- reporting ---------------------------------------------------------
    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.spans)

    @property
    def total_tokens(self) -> int:
        return sum(s.input_tokens + s.output_tokens for s in self.spans)

    def tree(self) -> str:
        by_parent: dict[str | None, list[Span]] = {}
        for span in self.spans:
            by_parent.setdefault(span.parent_id, []).append(span)

        lines: list[str] = []

        def walk(parent: str | None, depth: int) -> None:
            for span in by_parent.get(parent, []):
                mark = "x" if span.status == "error" else "-"
                cost = f" ${span.cost_usd:.4f}" if span.cost_usd else ""
                toks = (
                    f" {span.input_tokens}in/{span.output_tokens}out"
                    if span.input_tokens or span.output_tokens
                    else ""
                )
                extra = ""
                if span.name.startswith("decision:"):
                    extra = f"  -> {span.attributes.get('chosen')} ({span.attributes.get('because')})"
                lines.append(
                    f"{'  ' * depth}{mark} {span.name} [{span.duration_ms:.0f}ms]{toks}{cost}{extra}"
                )
                if span.error:
                    lines.append(f"{'  ' * (depth + 1)}! {span.error}")
                walk(span.span_id, depth + 1)

        walk(None, 0)
        lines.append(
            f"\ntotal: {self.total_tokens} tokens, ${self.total_cost_usd:.4f}, "
            f"{len(self.spans)} spans"
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "trace_id": s.trace_id,
                    "span_id": s.span_id,
                    "parent_id": s.parent_id,
                    "name": s.name,
                    "duration_ms": round(s.duration_ms, 2),
                    "status": s.status,
                    "error": s.error,
                    "model": s.model,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "cost_usd": round(s.cost_usd, 6),
                    "attributes": s.attributes,
                }
                for s in self.spans
            ],
            indent=2,
        )


def prompt_fingerprint(text: str) -> str:
    """Group runs by prompt version without storing the prompt."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]
