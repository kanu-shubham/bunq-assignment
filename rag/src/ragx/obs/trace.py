"""Minimal tracing: structured JSON logs + per-stage timings on every request.

Deliberately not OpenTelemetry — the shape is the same (trace id, spans,
attributes) so swapping in an OTel exporter is a change to `_emit` only, but
this keeps the demo dependency-free and the timings assertable in tests.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

_logger = logging.getLogger("ragx")


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.handlers = [handler]
    _logger.setLevel(level)
    _logger.propagate = False


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def current_trace_id() -> str:
    return _trace_id.get()


@contextmanager
def trace_context(trace_id: str | None = None) -> Iterator[str]:
    tid = trace_id or new_trace_id()
    previous = _trace_id.get()
    token = _trace_id.set(tid)
    try:
        yield tid
    finally:
        try:
            _trace_id.reset(token)
        except ValueError:
            # The block was entered and exited in different contexts — which is
            # exactly what happens when this wraps a generator that a server
            # resumes on a worker thread (SSE streaming). The token is not valid
            # there; restoring the value directly is.
            _trace_id.set(previous)


def log(event: str, **fields: Any) -> None:
    """One JSON object per line. `event` is the only required field."""
    payload = {"event": event, "trace_id": current_trace_id(), **fields}
    _logger.info(json.dumps(payload, default=str, separators=(",", ":")))


@dataclass
class Timings:
    """Accumulates per-stage wall time for a single request."""

    stages: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str, **fields: Any) -> Iterator[None]:
        started = time.perf_counter()
        error: str | None = None
        try:
            yield
        except Exception as exc:  # noqa: BLE001 - re-raised below
            error = type(exc).__name__
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.stages[name] = self.stages.get(name, 0.0) + elapsed_ms
            log("stage", stage=name, ms=round(elapsed_ms, 2), error=error, **fields)

    @property
    def total_ms(self) -> float:
        return sum(self.stages.values())

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 2) for k, v in self.stages.items()}
