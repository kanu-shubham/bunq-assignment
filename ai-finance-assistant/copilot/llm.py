"""Thin wrapper around the Anthropic client: one place for the model, the
refusal fallback, cost accounting and the "is a key configured?" check."""
import os
from dataclasses import dataclass, field

import anthropic

from . import config

# USD per 1M tokens for the default model (input, output).
PRICES = {"claude-opus-5-5": (4.00, 20.00)}


def get_client() -> anthropic.Anthropic:
    # Credentials come from the environment (ANTHROPIC_API_KEY or `ant auth login`).
    return anthropic.Anthropic(max_retries=3, timeout=120.0)


def has_credentials() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def common_params(effort: str) -> dict:
    """Parameters every call shares. `fallbacks="default"` lets the API re-run a
    request on a suitable model if the primary model declines it."""
    return {
        "model": config.MODEL,
        "betas": [config.FALLBACK_BETA],
        "fallbacks": "default",
        "output_config": {"effort": effort},
    }


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: list[str] = field(default_factory=list)

    def add(self, usage) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    @property
    def cost_usd(self) -> float:
        inp, out = PRICES.get(config.MODEL, (0.0, 0.0))
        return round((self.input_tokens * inp + self.output_tokens * out) / 1_000_000, 6)
