"""The model seam.

Every agent in this repo talks to an `LLM` protocol, never to a vendor SDK
directly. Two implementations ship here:

  * `AnthropicLLM`  — the real thing, official `anthropic` SDK, Claude Opus 5.
  * `ScriptedLLM`   — deterministic canned responses, zero network, zero cost.

That seam is the single most useful piece of architecture in an agent codebase
and a reliable interview answer: it makes the whole system testable. Multi-agent
systems are non-deterministic by construction; if your tests need a live model
you cannot assert on orchestration, routing, budgets, or deadlock handling. With
a scripted model you assert on all of them and reserve live calls for evals.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# Claude Opus 5. Thinking is on by default on this model; effort is the primary
# intelligence/latency/cost dial. Sampling params (temperature/top_p/top_k) are
# rejected with a 400 — steer with prompting instead.
DEFAULT_MODEL = "claude-opus-5"

# Cheaper worker for high-volume, low-judgment sub-agents (log scanning, field
# extraction). Mixing tiers is the main cost lever in a fan-out topology.
WORKER_MODEL = "claude-haiku-4-5"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = DEFAULT_MODEL
    stop_reason: str = "end_turn"

    def json(self, default: Any = None) -> Any:
        """Models wrap JSON in prose or fences often enough that every agent
        codebase grows this function. In production prefer structured outputs
        (`output_config={"format": {...}}`) so the shape is guaranteed."""
        return extract_json(self.text, default)


def extract_json(text: str, default: Any = None) -> Any:
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    return default


class LLM(Protocol):
    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        effort: str = "high",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion: ...

    def count_tokens(self, text: str) -> int: ...


class ScriptedLLM:
    """Deterministic stand-in.

    Routes on the first matching predicate over (system, last user message).
    Records every call so tests can assert on prompts — "did the critic actually
    see the policy?" is a real bug class and this is how you catch it.
    """

    def __init__(self, routes: list[tuple[Callable[[str, str], bool], str]], fallback: str = "{}"):
        self.routes = routes
        self.fallback = fallback
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        effort: str = "high",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = _as_text(msg.get("content"))
                break
        self.calls.append({"system": system, "user": last_user, "effort": effort})
        for predicate, response in self.routes:
            if predicate(system, last_user):
                return Completion(
                    response,
                    Usage(self.count_tokens(system + last_user), self.count_tokens(response)),
                    model="scripted",
                )
        return Completion(self.fallback, model="scripted")

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)


class AnthropicLLM:
    """Claude Opus 5 through the official SDK.

    Notes that matter on this model family and come up in review:
      * `thinking={"type": "adaptive"}` — `budget_tokens` is removed (400).
      * `output_config={"effort": ...}` is the cost/quality dial; effort lives
        inside `output_config`, not at the top level.
      * No `temperature`/`top_p`/`top_k` — they 400.
      * `cache_control` on the system prompt: agent loops resend a large, stable
        system prefix every turn, so this is usually the biggest cost win
        available. Minimum cacheable prefix on Opus 5 is 512 tokens.
      * Stream anything with a large `max_tokens` so you don't hit HTTP timeouts.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client: Any = None,
        cache_system_prompt: bool = True,
    ):
        if client is None:
            try:
                import anthropic  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - env dependent
                raise RuntimeError(
                    "pip install anthropic, or pass a ScriptedLLM for offline runs"
                ) from exc
            # Zero-arg constructor resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
            # or an `ant auth login` profile — do not hardcode a key.
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.cache_system_prompt = cache_system_prompt
        self.usage = Usage()

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        effort: str = "high",
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
        if self.cache_system_prompt:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        if tools:
            kwargs["tools"] = tools

        if max_tokens > 16_000:
            with self.client.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()
        else:
            response = self.client.messages.create(**kwargs)

        # Safety classifiers can decline with HTTP 200 + stop_reason "refusal".
        # Indexing content[0] unconditionally is the classic crash here.
        if response.stop_reason == "refusal":
            return Completion("", model=self.model, stop_reason="refusal")

        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(
                response.usage, "cache_creation_input_tokens", 0
            )
            or 0,
        )
        self.usage = self.usage + usage
        return Completion(text, usage, model=self.model, stop_reason=response.stop_reason)

    def count_tokens(self, text: str) -> int:
        """Exact counts come from the API, and they are model-specific. Never use
        tiktoken for Claude — it is OpenAI's tokenizer and undercounts badly."""
        try:
            resp = self.client.messages.count_tokens(
                model=self.model, messages=[{"role": "user", "content": text}]
            )
            return resp.input_tokens
        except Exception:  # pragma: no cover - network/offline fallback
            return estimate_tokens(text)


def estimate_tokens(text: str) -> int:
    """~4 chars/token. Fine for budget *accounting* between real counts; never
    for deciding whether a request will fit. Cache real counts, estimate deltas."""
    return max(1, len(text) // 4)


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return str(content)


def default_llm() -> LLM:
    """Live model when credentials are present, scripted otherwise, so demos and
    tests run identically on a laptop with no API key."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        try:
            return AnthropicLLM()
        except RuntimeError:
            pass
    return ScriptedLLM(routes=[], fallback="{}")
