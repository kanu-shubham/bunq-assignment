"""A scripted fake of the Anthropic client, so tests are free, fast and deterministic.
The LLM is a dependency like a database: inject it, then fake it in tests."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def text_block(text):
    return NS(type="text", text=text)


def tool_block(name, args, id="tu_1"):
    return NS(type="tool_use", name=name, input=args, id=id)


def message(content, stop_reason="end_turn", parsed=None):
    return NS(content=content, stop_reason=stop_reason, parsed_output=parsed,
              usage=NS(input_tokens=100, output_tokens=20))


class FakeClient:
    """Returns pre-scripted responses in order and records every request."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._next, parse=self._next))

    def _next(self, **kwargs):
        # Snapshot messages: the agent mutates its history list after the call.
        self.requests.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        return self.responses.pop(0)


@pytest.fixture
def fake_client():
    return FakeClient
