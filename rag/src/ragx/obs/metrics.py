"""In-process metrics with a Prometheus text exposition.

Enough to answer the questions that actually page someone: is the abstention
rate spiking, is the reranker failing open, what is p95 end-to-end, what are we
spending per answer.
"""

from __future__ import annotations

import threading
from bisect import insort
from collections import defaultdict
from collections.abc import Iterable

_LOCK = threading.Lock()
_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_HISTOGRAMS: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)

# Cost model, USD per million tokens (Claude Opus 5, list price).
INPUT_USD_PER_MTOK = 5.0
OUTPUT_USD_PER_MTOK = 25.0
CACHE_READ_USD_PER_MTOK = 0.5  # ~0.1x input


def _key(name: str, labels: dict[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((labels or {}).items()))


def incr(name: str, value: float = 1.0, **labels: str) -> None:
    with _LOCK:
        _COUNTERS[_key(name, labels)] += value


def observe(name: str, value: float, **labels: str) -> None:
    with _LOCK:
        insort(_HISTOGRAMS[_key(name, labels)], value)


def quantile(name: str, q: float, **labels: str) -> float:
    with _LOCK:
        values = _HISTOGRAMS.get(_key(name, labels), [])
        if not values:
            return 0.0
        idx = min(len(values) - 1, int(round(q * (len(values) - 1))))
        return values[idx]


def estimate_cost_usd(
    input_tokens: int, output_tokens: int, cache_read_tokens: int = 0
) -> float:
    return (
        input_tokens * INPUT_USD_PER_MTOK
        + output_tokens * OUTPUT_USD_PER_MTOK
        + cache_read_tokens * CACHE_READ_USD_PER_MTOK
    ) / 1_000_000


def reset() -> None:
    with _LOCK:
        _COUNTERS.clear()
        _HISTOGRAMS.clear()


def _fmt_labels(labels: Iterable[tuple[str, str]]) -> str:
    items = list(labels)
    if not items:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in items)
    return "{" + inner + "}"


def render_prometheus() -> str:
    lines: list[str] = []
    with _LOCK:
        for (name, labels), value in sorted(_COUNTERS.items()):
            lines.append(f"ragx_{name}{_fmt_labels(labels)} {value}")
        for (name, labels), values in sorted(_HISTOGRAMS.items()):
            if not values:
                continue
            label_list = list(labels)
            for q in (0.5, 0.95, 0.99):
                idx = min(len(values) - 1, int(round(q * (len(values) - 1))))
                quantile_labels = [*label_list, ("quantile", str(q))]
                lines.append(f"ragx_{name}{_fmt_labels(quantile_labels)} {values[idx]:.3f}")
            lines.append(f"ragx_{name}_count{_fmt_labels(label_list)} {len(values)}")
    return "\n".join(lines) + "\n"
