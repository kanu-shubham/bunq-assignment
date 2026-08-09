"""The extraction pipeline: call, salvage, validate, repair, ground.

The shape that matters:

    for attempt in 1..N:
        response = provider.complete(conversation)
        if refused:            -> stop, record the refusal
        payload = salvage(response.text)      # fences, prose, trailing commas
        errors  = validate(payload)           # Pydantic
        if errors:            -> append the model's own output + the validator's
                                 messages to the conversation and try again
        else:                 -> optionally ground-check, optionally repair again

Two design notes worth arguing about:

*   The repair turn feeds back the *validator's* message, not a paraphrase.
    "line_items.0.amount: Input should be a valid number" is actionable;
    "your JSON was wrong" is not.
*   Salvage runs before validation.  A response wrapped in a markdown fence is a
    formatting miss, not a content miss, and burning a repair round-trip on it
    teaches you nothing.  `salvage_json` records what it had to do, so the
    report can separate "drifted but recoverable" from "genuinely broken".
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from pydantic import ValidationError

from . import grounding, prompts
from .client import LLMResponse, Provider
from .schemas import format_config, json_schema_for, model_for


@dataclass
class Attempt:
    index: int
    kind: str  # "initial" | "schema_repair" | "grounding_repair"
    raw_text: str
    salvage_actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)
    refused: bool = False
    api_error: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    doc_id: str
    kind: str
    status: str  # ok | invalid | refused | error
    payload: Optional[dict[str, Any]]
    attempts: list[Attempt]
    followed_injection: bool = False
    model: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def repairs_used(self) -> int:
        return max(0, len(self.attempts) - 1)

    @property
    def valid_first_try(self) -> bool:
        return bool(self.attempts) and not self.attempts[0].errors and not self.attempts[0].refused

    @property
    def clean_first_try(self) -> bool:
        """Valid *and* free of format drift on the first attempt."""
        return self.valid_first_try and not self.attempts[0].salvage_actions

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "kind": self.kind,
            "status": self.status,
            "payload": self.payload,
            "attempts": [asdict(a) for a in self.attempts],
            "repairs_used": self.repairs_used,
            "valid_first_try": self.valid_first_try,
            "clean_first_try": self.clean_first_try,
            "followed_injection": self.followed_injection,
            "model": self.model,
            "config": self.config,
        }


@dataclass
class ExtractConfig:
    prompt: str = "careful"
    output_mode: str = "strict"  # strict (structured outputs) | freeform
    max_attempts: int = 3
    repair_ungrounded: bool = True
    temperature: Optional[float] = None


# --------------------------------------------------------------------------- #
# Salvage
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def salvage_json(text: str) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Recover a JSON object from a response that wandered off-format.

    Returns the object (or None) and the list of recoveries that were needed —
    that list is the format-drift measurement.
    """
    actions: list[str] = []
    if not text or not text.strip():
        return None, ["empty_response"]

    candidate = text.strip()

    fence = _FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
        actions.append("markdown_fence")

    if not candidate.startswith("{"):
        start = candidate.find("{")
        if start > 0:
            actions.append("prose_preamble")
            candidate = candidate[start:]

    def attempt(payload: str) -> Optional[dict[str, Any]]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    parsed = attempt(candidate)
    if parsed is None:
        # Balance braces to cut off trailing commentary.
        depth, end, in_string, escaped = 0, None, False, False
        for i, ch in enumerate(candidate):
            if escaped:
                escaped = False
                continue
            if ch == "\\" and in_string:
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
        if end is not None and end < len(candidate.rstrip()):
            actions.append("trailing_commentary")
            parsed = attempt(candidate[:end])
        elif end is not None:
            parsed = attempt(candidate[:end])

    if parsed is None:
        repaired = re.sub(r",(\s*[}\]])", r"\1", candidate)
        if repaired != candidate:
            parsed = attempt(repaired)
            if parsed is not None:
                actions.append("trailing_comma")

    if parsed is None:
        actions.append("unparseable")
        return None, actions
    return parsed, actions


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate(payload: dict[str, Any], kind: str) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Validate against the Pydantic model, returning normalised data or errors."""
    model = model_for(kind)
    try:
        instance = model.model_validate(payload)
    except ValidationError as exc:
        messages = []
        for err in exc.errors():
            location = ".".join(str(p) for p in err["loc"]) or "<root>"
            messages.append(f"{location}: {err['msg']}")
        return None, messages
    return instance.model_dump(), []


def _refusal_in_text(text: str) -> bool:
    """A soft refusal: HTTP 200, no `refusal` stop reason, prose declining.

    Cheap heuristic, and it only fires when nothing JSON-shaped came back — a
    valid object with an apologetic preamble is drift, not a refusal.
    """
    if "{" in text:
        return False
    lowered = text.lower()
    markers = (
        "i can't", "i cannot", "i'm not able", "i am not able", "i won't",
        "unable to help", "i'd rather not", "not appropriate",
    )
    return any(marker in lowered for marker in markers)


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def extract(
    *,
    doc_id: str,
    kind: str,
    document_text: str,
    provider: Provider,
    config: ExtractConfig,
    injection_canary: Optional[str] = None,
) -> ExtractionResult:
    model_cls = model_for(kind)
    schema = json_schema_for(model_cls)
    system = prompts.system_prompt(config.prompt, output_mode=config.output_mode, schema=schema)
    output_config = format_config(model_cls) if config.output_mode == "strict" else None

    conversation: list[dict[str, Any]] = [
        {"role": "user", "content": prompts.user_prompt(kind, document_text)}
    ]
    attempts: list[Attempt] = []
    payload: Optional[dict[str, Any]] = None
    status = "invalid"
    attempt_kind = "initial"

    for index in range(config.max_attempts):
        response: LLMResponse = provider.complete(
            system=system,
            messages=conversation,
            output_config=output_config,
            temperature=config.temperature,
        )
        attempt = Attempt(index=index, kind=attempt_kind, raw_text=response.text, usage=response.usage)

        if response.error:
            attempt.api_error = response.error
            attempts.append(attempt)
            status = "error"
            break

        if response.refused or _refusal_in_text(response.text):
            attempt.refused = True
            attempts.append(attempt)
            status = "refused"
            break

        candidate, actions = salvage_json(response.text)
        attempt.salvage_actions = actions

        if candidate is None:
            attempt.errors = ["response did not contain a JSON object"]
        else:
            validated, errors = validate(candidate, kind)
            attempt.errors = errors
            if not errors:
                payload = validated

        if payload is not None and config.repair_ungrounded:
            report = grounding.check(payload, document_text)
            attempt.ungrounded = report.ungrounded

        attempts.append(attempt)

        if payload is not None and not attempt.ungrounded:
            status = "ok"
            break

        if index == config.max_attempts - 1:
            status = "ok" if payload is not None else "invalid"
            break

        # Build the repair turn.  The model sees its own output verbatim.
        conversation = conversation + [{"role": "assistant", "content": response.text or "(empty)"}]
        if payload is None:
            conversation.append(
                {"role": "user", "content": prompts.repair_prompt(response.text, attempt.errors, kind=kind)}
            )
            attempt_kind = "schema_repair"
        else:
            conversation.append(
                {"role": "user", "content": prompts.grounding_repair_prompt(attempt.ungrounded, kind=kind)}
            )
            attempt_kind = "grounding_repair"
            payload = None  # re-earned on the next pass

    if payload is None and status == "ok":  # pragma: no cover - defensive
        status = "invalid"

    return ExtractionResult(
        doc_id=doc_id,
        kind=kind,
        status=status,
        payload=payload,
        attempts=attempts,
        followed_injection=grounding.followed_injection(payload or {}, injection_canary),
        model=getattr(provider, "model", ""),
        config={
            "prompt": config.prompt,
            "output_mode": config.output_mode,
            "max_attempts": config.max_attempts,
            "repair_ungrounded": config.repair_ungrounded,
            "temperature": config.temperature,
            "provider": getattr(provider, "name", "?"),
        },
    )
