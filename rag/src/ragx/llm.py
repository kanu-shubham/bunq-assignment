"""Claude access layer.

Everything that talks to the model goes through here so that four concerns are
handled once instead of in three call sites:

* **Prompt caching.** The system prompt and the tool-free instruction block are
  stable across every request; the question and the retrieved context are not.
  Caching is a *prefix* match, so the stable part is passed as its own system
  block with a cache breakpoint and the volatile part goes into the user turn.
  Get this ordering wrong and the cache silently never hits.
* **Refusals.** Claude Opus 5 can return HTTP 200 with
  `stop_reason == "refusal"` and an empty `content` array. Code that reads
  `content[0].text` unconditionally crashes on it. We check `stop_reason` first
  and opt into server-side fallbacks so a declined request is re-run on
  Anthropic's recommended fallback model instead of failing the user.
* **Streaming.** Answer synthesis streams; anything above ~16k `max_tokens`
  must stream anyway to avoid SDK HTTP timeouts.
* **Testability.** `LLM` is a protocol. Offline tests and CI use `ScriptedLLM`,
  so the whole pipeline including grounding checks is exercisable with no API
  key and no network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .config import FALLBACK_BETA
from .obs import metrics
from .obs.trace import log
from .types import Usage


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"
    model: str = ""

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


@runtime_checkable
class LLM(Protocol):
    def complete(
        self,
        *,
        model: str,
        system: Sequence[dict[str, Any]],
        user: str,
        max_tokens: int,
        effort: str = "medium",
        thinking: bool = True,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        *,
        model: str,
        system: Sequence[dict[str, Any]],
        user: str,
        max_tokens: int,
        effort: str = "medium",
        thinking: bool = True,
        timeout_s: float | None = None,
    ) -> Iterator[str]: ...


def cached_system_block(text: str) -> dict[str, Any]:
    """A stable system block with a cache breakpoint on it.

    Minimum cacheable prefix on Claude Opus 5 is 512 tokens; shorter prefixes
    silently do not cache (no error, `cache_creation_input_tokens: 0`).
    """
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def plain_system_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class AnthropicLLM:
    """Thin wrapper over the Anthropic SDK.

    Note the parameter choices, all of which are current-model specific:
      * no `temperature` / `top_p` / `top_k` — removed on Claude Opus 5 (400).
      * no `budget_tokens` — removed; depth is controlled by `effort`.
      * `thinking: {"type": "adaptive"}` — on by default on Opus 5; disabling it
        is only legal at effort <= "high", and disabled thinking on this model
        can emit tool calls as plain text, so we leave it on and use low effort
        when we want to spend less.
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        max_retries: int = 3,
        timeout_s: float = 60.0,
        use_server_fallback: bool = True,
    ) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(max_retries=max_retries, timeout=timeout_s)
        self._client = client
        self._use_server_fallback = use_server_fallback

    def _request_kwargs(
        self,
        *,
        model: str,
        system: Sequence[dict[str, Any]],
        user: str,
        max_tokens: int,
        effort: str,
        thinking: bool,
        json_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        output_config: dict[str, Any] = {"effort": effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": list(system),
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if self._use_server_fallback:
            # On a policy decline, re-run server-side on the recommended
            # fallback model rather than handing the user a refusal.
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def complete(
        self,
        *,
        model: str,
        system: Sequence[dict[str, Any]],
        user: str,
        max_tokens: int,
        effort: str = "medium",
        thinking: bool = True,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        kwargs = self._request_kwargs(
            model=model,
            system=system,
            user=user,
            max_tokens=max_tokens,
            effort=effort,
            thinking=thinking,
            json_schema=json_schema,
        )
        client = self._client
        if timeout_s is not None:
            client = client.with_options(timeout=timeout_s)
        response = client.beta.messages.create(**kwargs)
        return _to_llm_response(response)

    def stream(
        self,
        *,
        model: str,
        system: Sequence[dict[str, Any]],
        user: str,
        max_tokens: int,
        effort: str = "medium",
        thinking: bool = True,
        timeout_s: float | None = None,
    ) -> Iterator[str]:
        kwargs = self._request_kwargs(
            model=model,
            system=system,
            user=user,
            max_tokens=max_tokens,
            effort=effort,
            thinking=thinking,
            json_schema=None,
        )
        client = self._client
        if timeout_s is not None:
            client = client.with_options(timeout=timeout_s)
        with client.beta.messages.stream(**kwargs) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
        _record_usage(_to_llm_response(final))


def _to_llm_response(message: Any) -> LLMResponse:
    stop_reason = getattr(message, "stop_reason", "end_turn") or "end_turn"
    text_parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    raw_usage = getattr(message, "usage", None)
    usage = Usage(
        input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
        output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
    )
    response = LLMResponse(
        text="".join(text_parts),
        usage=usage,
        stop_reason=stop_reason,
        model=getattr(message, "model", ""),
    )
    _record_usage(response)
    if response.refused:
        details = getattr(message, "stop_details", None)
        log("llm_refusal", category=getattr(details, "category", None))
        metrics.incr("llm_refusals_total")
    return response


def _record_usage(response: LLMResponse) -> None:
    usage = response.usage
    metrics.incr("llm_input_tokens_total", usage.input_tokens, model=response.model or "?")
    metrics.incr("llm_output_tokens_total", usage.output_tokens, model=response.model or "?")
    metrics.incr(
        "llm_cache_read_tokens_total", usage.cache_read_input_tokens, model=response.model or "?"
    )
    metrics.incr(
        "llm_cost_usd_total",
        metrics.estimate_cost_usd(
            usage.input_tokens, usage.output_tokens, usage.cache_read_input_tokens
        ),
        model=response.model or "?",
    )


_JSON_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def parse_json_response(text: str) -> Any:
    """Structured outputs guarantee schema-valid JSON, but this system also runs
    against fakes and older models, and a truncated response (`max_tokens`) is
    always possible. Fail loudly with the raw text attached rather than silently
    returning an empty result that looks like "no relevant documents"."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_RE.search(text)
        if not match:
            raise ValueError(f"no JSON object in model output: {text[:200]!r}") from None
        return json.loads(match.group(0))


class ScriptedLLM:
    """Deterministic offline fake used by tests, CI, and the demo.

    `handler` receives (model, user_prompt) and returns the response text, so a
    test can express "the model cites [2] and [4]" or "the model returns
    malformed JSON" without touching the network.
    """

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        model: str,
        system: Sequence[dict[str, Any]],
        user: str,
        max_tokens: int,
        effort: str = "medium",
        thinking: bool = True,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        self.calls.append({"model": model, "user": user, "system": list(system)})
        text = self._handler(model, user)
        if isinstance(text, LLMResponse):
            return text
        return LLMResponse(text=text, usage=Usage(1, 1), model=model)

    def stream(
        self,
        *,
        model: str,
        system: Sequence[dict[str, Any]],
        user: str,
        max_tokens: int,
        effort: str = "medium",
        thinking: bool = True,
        timeout_s: float | None = None,
    ) -> Iterator[str]:
        response = self.complete(
            model=model,
            system=system,
            user=user,
            max_tokens=max_tokens,
            effort=effort,
            thinking=thinking,
        )
        for word in response.text.split(" "):
            yield word + " "
