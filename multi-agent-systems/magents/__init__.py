"""magents — a teaching implementation of multi-agent collaboration and memory.

Pure standard library (the `anthropic` SDK is optional and only needed for live
model calls), so every demo and test runs offline and deterministically.
"""

from .graph import (
    END,
    START,
    Checkpoint,
    CompiledGraph,
    InMemoryCheckpointer,
    Interrupt,
    RecursionLimit,
    StateGraph,
    add,
    append,
    last_write_wins,
    merge_dict,
    unique_append,
)
from .llm import AnthropicLLM, Completion, LLM, ScriptedLLM, Usage, default_llm
from .observability import Tracer

__all__ = [
    "END",
    "START",
    "AnthropicLLM",
    "Checkpoint",
    "CompiledGraph",
    "Completion",
    "InMemoryCheckpointer",
    "Interrupt",
    "LLM",
    "RecursionLimit",
    "ScriptedLLM",
    "StateGraph",
    "Tracer",
    "Usage",
    "add",
    "append",
    "default_llm",
    "last_write_wins",
    "merge_dict",
    "unique_append",
]
