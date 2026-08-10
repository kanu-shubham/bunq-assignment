"""Minimal model layer shared by the six prototypes.

The block shapes below (`text`, `tool_use`, `tool_result`) mirror the Anthropic
Messages API one-for-one, so the loops in the prototypes are the loops you would
write against a real model. Two implementations of the same protocol:

  ScriptedModel  - deterministic, offline, no API key. Used by the demos so they
                   run anywhere and produce the same transcript every time.
  AnthropicModel - the real thing. Same interface, so swapping it in is a
                   one-line change in each prototype.

Run this file directly for a smoke test of the scripted path.
"""

from __future__ import annotations

import itertools
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

# --------------------------------------------------------------------------
# Wire types
# --------------------------------------------------------------------------

Block = dict[str, Any]
Message = dict[str, Any]

_ids = itertools.count(1)


def new_id(prefix: str = "toolu") -> str:
    return f"{prefix}_{next(_ids):04d}"


def text_block(text: str) -> Block:
    return {"type": "text", "text": text}


def tool_use_block(name: str, tool_input: dict[str, Any], block_id: str | None = None) -> Block:
    return {"type": "tool_use", "id": block_id or new_id(), "name": name, "input": tool_input}


def tool_result_block(tool_use_id: str, content: str, is_error: bool = False) -> Block:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def user(*blocks: Block | str) -> Message:
    return {"role": "user", "content": [text_block(b) if isinstance(b, str) else b for b in blocks]}


def assistant(*blocks: Block | str) -> Message:
    return {
        "role": "assistant",
        "content": [text_block(b) if isinstance(b, str) else b for b in blocks],
    }


def blocks_of(message: Message, kind: str) -> list[Block]:
    return [b for b in message["content"] if b.get("type") == kind]


def text_of(message: Message) -> str:
    return "\n".join(b["text"] for b in blocks_of(message, "text")).strip()


def transcript_text(messages: Iterable[Message]) -> str:
    """Flatten a transcript to a plain string. Handy for scripted policies."""
    out = []
    for m in messages:
        for b in m["content"]:
            if b["type"] == "text":
                out.append(f"{m['role']}: {b['text']}")
            elif b["type"] == "tool_use":
                out.append(f"{m['role']}: CALL {b['name']}({json.dumps(b['input'])})")
            elif b["type"] == "tool_result":
                flag = "ERROR " if b.get("is_error") else ""
                out.append(f"{m['role']}: RESULT {flag}{b['content']}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Model protocol
# --------------------------------------------------------------------------


@dataclass
class Reply:
    """One assistant turn. `stop_reason` is what drives the agent loop."""

    blocks: list[Block]
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens"

    @property
    def message(self) -> Message:
        return {"role": "assistant", "content": self.blocks}

    @property
    def tool_calls(self) -> list[Block]:
        return [b for b in self.blocks if b["type"] == "tool_use"]

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.blocks if b["type"] == "text").strip()


class Model(Protocol):
    def respond(
        self,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Reply: ...


# --------------------------------------------------------------------------
# Offline implementation
# --------------------------------------------------------------------------

Policy = Callable[[str, list[Message]], Reply]


@dataclass
class ScriptedModel:
    """A deterministic stand-in for a model.

    `policy(system, messages) -> Reply` plays the role the model would play.
    Every prototype writes its own tiny policy, which doubles as an executable
    description of the behaviour the pattern assumes from the model.
    """

    policy: Policy
    calls: int = field(default=0, init=False)

    def respond(self, system, messages, tools=None) -> Reply:
        self.calls += 1
        return self.policy(system, messages)


def say(text: str) -> Reply:
    return Reply([text_block(text)], "end_turn")


def call(name: str, **tool_input: Any) -> Reply:
    return Reply([tool_use_block(name, tool_input)], "tool_use")


def think_and_call(thought: str, name: str, **tool_input: Any) -> Reply:
    return Reply([text_block(thought), tool_use_block(name, tool_input)], "tool_use")


# --------------------------------------------------------------------------
# Real implementation
# --------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")


@dataclass
class AnthropicModel:
    """The same interface, backed by the Messages API.

        pip install anthropic && export ANTHROPIC_API_KEY=...

    Note the parts that are load-bearing for every pattern in this repo:
      * `stop_reason == "tool_use"` is the loop condition;
      * the assistant message is appended to history *verbatim* (dropping a
        tool_use block breaks the next turn);
      * every tool_use id must come back as exactly one tool_result.
    """

    model: str = DEFAULT_MODEL
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        import anthropic  # imported lazily so the demos run without the SDK

        self._client = anthropic.Anthropic()

    def respond(self, system, messages, tools=None) -> Reply:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools or [],
            messages=messages,
        )
        blocks = [b.model_dump() for b in resp.content]
        return Reply(blocks, resp.stop_reason)


# --------------------------------------------------------------------------

if __name__ == "__main__":
    def policy(system: str, messages: list[Message]) -> Reply:
        if "RESULT" in transcript_text(messages):
            return say("It is 12 degrees in Amsterdam.")
        return think_and_call("I need the weather.", "get_weather", city="Amsterdam")

    model = ScriptedModel(policy)
    history = [user("What is the weather in Amsterdam?")]
    reply = model.respond("You are helpful.", history)
    print(reply.stop_reason, reply.tool_calls)
    history += [reply.message, user(tool_result_block(reply.tool_calls[0]["id"], "12C, clear"))]
    print(model.respond("You are helpful.", history).text)
