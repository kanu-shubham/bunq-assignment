"""Token estimation.

Chunking needs a token count for *every* chunk boundary decision, so it cannot
call a network API per candidate boundary. We use a cheap local estimator and
calibrate it offline against `client.messages.count_tokens`, which is the only
accurate counter for Claude models (tiktoken is OpenAI's tokenizer and
undercounts Claude by 15–20%, more on code).

The estimator is intentionally conservative — it over-counts slightly — so a
"512 token" chunk never blows a real budget.
"""

from __future__ import annotations

import re
from functools import lru_cache

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

# Words longer than this tend to split into multiple BPE tokens.
_CHARS_PER_SUBTOKEN = 4


@lru_cache(maxsize=8192)
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    total = 0
    for piece in _TOKEN_RE.findall(text):
        total += max(1, (len(piece) + _CHARS_PER_SUBTOKEN - 1) // _CHARS_PER_SUBTOKEN)
    # Whitespace and newlines are not free in BPE; add a small structural margin.
    return total + text.count("\n") // 2 + 1


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Cheap prefix truncation used for reranker snippets and log fields."""
    if estimate_tokens(text) <= max_tokens:
        return text
    words = text.split()
    lo, hi = 0, len(words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(" ".join(words[:mid])) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return " ".join(words[:lo])


def count_tokens_api(client, model: str, text: str) -> int:
    """Accurate count via the Anthropic API. Used by the calibration script and
    by budget checks on the final prompt — never in the chunking hot loop."""
    resp = client.messages.count_tokens(
        model=model, messages=[{"role": "user", "content": text}]
    )
    return resp.input_tokens
