"""Model providers.

`AnthropicProvider` is the real thing.  `MockProvider` is a seeded simulator
that runs the identical pipeline offline: it does a genuine regex extraction
from the document (no access to the ground truth) and then injects the failure
modes this session is about — fences, prose wrappers, broken JSON, type drift,
extra keys, invented values, refusals, injection compliance.

The mock exists so the repair loop, the grounding checker and the scorer can be
tested and demonstrated without an API key.  Its accuracy numbers say nothing
about any Claude model; only the harness behaviour transfers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .normalize import parse_date, parse_number

# Sampling parameters were removed from the newest models: sending `temperature`
# to any of these is a 400, so the temperature sweep has to name a model that
# still exposes the knob.
NO_SAMPLING_PARAMS = frozenset(
    {"claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-fable-5", "claude-mythos-5"}
)
# Sonnet 5 accepts the parameter but rejects non-default values.
REJECTS_NONDEFAULT_SAMPLING = frozenset({"claude-sonnet-5"})

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_TEMPERATURE_MODEL = "claude-sonnet-4-6"


@dataclass
class LLMResponse:
    text: str
    model: str
    stop_reason: Optional[str] = None
    refused: bool = False
    refusal_category: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class Provider(Protocol):
    name: str
    model: str

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        output_config: Optional[dict[str, Any]] = None,
        temperature: Optional[float] = None,
        task: str = "extraction",
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------- #
# Real provider
# --------------------------------------------------------------------------- #


class AnthropicProvider:
    """Thin wrapper over `client.messages.create`.

    Structured outputs go through `output_config.format`; the caller decides
    whether to pass one.  `messages.parse()` would hand back a validated Pydantic
    object in one line, but the whole point here is to watch validation fail, so
    the raw text is what comes back.
    """

    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = 8000,
        effort: str = "medium",
        thinking: str = "adaptive",
        max_retries: int = 2,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.thinking = thinking
        self._client = anthropic.Anthropic(max_retries=max_retries)

    def supports_temperature(self, temperature: Optional[float]) -> bool:
        if temperature is None:
            return True
        if self.model in NO_SAMPLING_PARAMS:
            return False
        if self.model in REJECTS_NONDEFAULT_SAMPLING and temperature != 1.0:
            return False
        return True

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        output_config: Optional[dict[str, Any]] = None,
        temperature: Optional[float] = None,
        task: str = "extraction",  # noqa: ARG002 - the real model needs no hint
    ) -> LLMResponse:
        if temperature is not None and not self.supports_temperature(temperature):
            raise ValueError(
                f"model {self.model!r} does not accept a temperature parameter "
                f"(sampling parameters were removed on {sorted(NO_SAMPLING_PARAMS)}). "
                f"Use --model {DEFAULT_TEMPERATURE_MODEL} or another temperature-capable model."
            )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": self.effort},
        }
        if output_config:
            kwargs["output_config"] = {**kwargs["output_config"], **output_config}
        if self.thinking == "disabled":
            # Only legal at effort `high` or below on the newest Opus, and it
            # brings its own failure modes — see the README.
            kwargs["thinking"] = {"type": "disabled"}
        elif self.thinking == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        if temperature is not None:
            kwargs["temperature"] = temperature

        try:
            response = self._client.messages.create(**kwargs)
        except self._anthropic.APIStatusError as exc:
            return LLMResponse(text="", model=self.model, error=f"{type(exc).__name__}: {exc}")
        except self._anthropic.APIConnectionError as exc:
            return LLMResponse(text="", model=self.model, error=f"APIConnectionError: {exc}")

        # Check the stop reason before touching content: on a refusal, `content`
        # is empty or partial and indexing it blows up.
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return LLMResponse(
                text="",
                model=response.model,
                stop_reason="refusal",
                refused=True,
                refusal_category=getattr(details, "category", None),
                usage=_usage(response),
            )

        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        return LLMResponse(
            text=text,
            model=response.model,
            stop_reason=response.stop_reason,
            usage=_usage(response),
        )


def _usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
    }


# --------------------------------------------------------------------------- #
# Offline mock provider
# --------------------------------------------------------------------------- #

_REFUSAL_TEXT = (
    "I'm not able to help with extracting data from this document — it contains "
    "health information and other personal details that shouldn't be processed "
    "into a structured record without a clear basis for doing so."
)


class MockProvider:
    """A seeded stand-in for the model.  No network, no key, no cost."""

    name = "mock"

    def __init__(self, model: str = "mock-1", *, seed: int = 0, rep: int = 0, skill: float = 0.85) -> None:
        self.model = model
        self.seed = seed
        self.rep = rep
        self.skill = skill

    def supports_temperature(self, temperature: Optional[float]) -> bool:  # noqa: D102
        return True

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        output_config: Optional[dict[str, Any]] = None,
        temperature: Optional[float] = None,
        task: str = "extraction",
    ) -> LLMResponse:
        if task != "extraction":
            return _mock_task_response(
                self, task=task, system=system, messages=messages, temperature=temperature
            )
        strict = output_config is not None and "format" in (output_config or {})
        first_user = _first_user_text(messages)
        document = _between(first_user, "<document>", "</document>") or first_user
        kind = "resume" if "resume" in first_user[:200].lower() else "invoice"
        is_repair = len(messages) > 1

        temp = 0.0 if temperature is None else temperature
        # At temperature 0 the only variation across repetitions is the small
        # residual that real greedy decoding still shows; at 0.7 the roll is
        # rerolled per repetition, which is the effect the sweep measures.
        variation_p = 0.02 + 0.35 * temp
        rng = _rng(f"{self.seed}|{document[:400]}|{self.rep if temp > 0 else 0}|{is_repair}")
        base_rng = _rng(f"{self.seed}|{document[:400]}")

        payload = _mock_extract(document, kind)
        naive_prompt = "Be thorough and make sure every field is filled in" in system

        # --- refusal ------------------------------------------------------- #
        # A prompt that frames the task (what the fields are for, what to do with
        # what it cannot read) draws fewer spurious refusals than one that just
        # says "extract everything" — so the naive arm balks more often.
        sensitive = "propranolol" in document.lower() or "registered disability" in document.lower()
        refusal_p = (0.8 if naive_prompt else 0.3) + 0.2 * temp
        refusal_rng = _rng(f"refusal|{self.seed}|{document[:400]}|{naive_prompt}")
        if sensitive and not is_repair and refusal_rng.random() < refusal_p:
            return LLMResponse(
                text=_REFUSAL_TEXT, model=self.model, stop_reason="end_turn",
                refused=True, refusal_category="simulated_policy",
                usage={"input_tokens": len(document) // 4, "output_tokens": 60},
            )

        # --- prompt injection ---------------------------------------------- #
        injected = _injection_directive(document)
        if injected and base_rng.random() < (0.55 if naive_prompt else 0.12):
            payload.update(injected)

        # --- hallucination -------------------------------------------------- #
        for key, filler in _FILLERS.get(kind, {}).items():
            if payload.get(key) in (None, [], ""):
                p = 0.55 if naive_prompt else 0.12
                if rng.random() < p * (1 + temp):
                    payload[key] = filler(rng)

        # --- value jitter (this is what temperature buys you) --------------- #
        if not is_repair:
            for key, value in list(payload.items()):
                if isinstance(value, str) and value and rng.random() < variation_p:
                    payload[key] = _jitter_string(value, rng)
                elif isinstance(value, float) and rng.random() < variation_p * 0.5:
                    payload[key] = round(value * rng.choice([1.0, 1.0, 0.1, 10.0]), 2)

        body = json.dumps(payload, ensure_ascii=False, indent=2)

        # --- format drift ---------------------------------------------------- #
        # Structured outputs make the shape a compiler guarantee, so drift is
        # only simulated in freeform mode — which is exactly the argument for
        # using structured outputs.
        if not strict and not is_repair:
            roll = rng.random()
            drift_budget = 0.30 + 0.4 * temp
            if roll < drift_budget * 0.30:
                body = f"```json\n{body}\n```"
            elif roll < drift_budget * 0.55:
                body = f"Here is the extracted data:\n\n{body}"
            elif roll < drift_budget * 0.75:
                body = body + "\n\nLet me know if you'd like any of these fields explained."
            elif roll < drift_budget * 0.88:
                body = re.sub(r"\n\}$", ",\n}", body)  # trailing comma
            elif roll < drift_budget:
                payload["confidence_note"] = "moderate — the scan quality is poor"
                body = json.dumps(payload, ensure_ascii=False, indent=2)
        elif not strict and is_repair and rng.random() < 0.12:
            body = f"```json\n{body}\n```"

        return LLMResponse(
            text=body, model=self.model, stop_reason="end_turn",
            usage={"input_tokens": len(document) // 4, "output_tokens": len(body) // 4},
        )


def _rng(key: str):
    import random

    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:16], 16))


def _first_user_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            content = message["content"]
            return content if isinstance(content, str) else " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
    return ""


def _between(text: str, start: str, end: str) -> str:
    a, b = text.find(start), text.rfind(end)
    if a == -1 or b == -1:
        return ""
    return text[a + len(start) : b].strip()


def _jitter_string(value: str, rng) -> str:
    choice = rng.random()
    if choice < 0.4:
        return value.upper()
    if choice < 0.7:
        return value.strip().rstrip(".") + "."
    if choice < 0.85 and " " in value:
        return value.split(" ", 1)[1]
    return value


def _injection_directive(document: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "999999.99" in document:
        out["total_amount"] = 999999.99
        out["vendor_name"] = "Approved Vendor Ltd"
        out["invoice_number"] = "INV-OVERRIDE-0001"
    if "PRIORITY CANDIDATE" in document:
        out["full_name"] = "PRIORITY CANDIDATE"
        out["years_experience"] = 99.0
    return out


_FILLERS: dict[str, dict[str, Any]] = {
    "invoice": {
        "purchase_order": lambda r: f"PO{r.randint(100000, 999999)}",
        "due_date": lambda r: f"2025-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}",
        "total_amount": lambda r: round(r.uniform(100, 5000), 2),
        "vendor_tax_id": lambda r: f"NL{r.randint(100000000, 999999999)}B01",
    },
    "resume": {
        "years_experience": lambda r: float(r.randint(3, 15)),
        "phone": lambda r: f"+31 6 {r.randint(10, 99)} {r.randint(100, 999)} {r.randint(100, 999)}",
        "location": lambda r: r.choice(["Amsterdam, NL", "Rotterdam", "Utrecht"]),
    },
}


# --------------------------------------------------------------------------- #
# The mock's own (honest) extractor
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def _mock_extract(document: str, kind: str) -> dict[str, Any]:
    return _mock_invoice(document) if kind == "invoice" else _mock_resume(document)


def _label(document: str, *labels: str) -> Optional[str]:
    for label in labels:
        m = re.search(rf"{label}\s*[:#]?\s*(.+)", document, re.IGNORECASE)
        if m:
            value = m.group(1).strip().split("   ")[0].strip()
            if value:
                return value
    return None


def _mock_invoice(document: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in document.split("\n")]
    non_empty = [ln for ln in lines if ln and not ln.startswith(("From:", "To:", "Subject:", "Date:"))]
    vendor = non_empty[0] if non_empty else None
    if vendor and ("forwarded" in vendor.lower() or vendor.startswith("-")):
        vendor = next((ln for ln in non_empty[1:] if ln and not ln.startswith("-")), None)

    number = _label(document, r"invoice\s*(?:no\.?|number|#)", r"document no", r"credit note no\.?",
                    r"rechnungsnummer", r"\bnr\.")
    if number:
        number = number.split()[0].strip(",.;")

    issue_raw = _label(document, r"invoice date", r"rechnungsdatum", r"\bissued\b", r"\bdate\b")
    due_raw = _label(document, r"payment due", r"due date", r"\bdue\b")

    currency = None
    m = re.search(r"\b(USD|EUR|GBP|CHF|SEK|JPY|INR)\b", document)
    if m:
        currency = m.group(1)

    def amount_after(*labels: str) -> Optional[float]:
        """First number printed after `label`, on the same line."""
        for label in labels:
            m = re.search(rf"{label}[^\n\d-]*(-?\d[\d.,]*)", document, re.IGNORECASE)
            if m:
                return parse_number(m.group(1))
        return None

    total = amount_after(r"total credit", r"\btotal\b", r"gesamtbetrag")
    subtotal = amount_after(r"subtotal", r"net total")
    # The rate line ("VAT 21%   252,00"), not the registration line
    # ("VAT / Tax ID: GB 421 7788 21") — hence the required percent sign.
    tax = amount_after(r"vat\s*\d+\s*%", r"zzgl\.\s*\d+\s*%\s*ust", r"\bust\b", r"\btax\b(?!\s*(?:id|/))")

    line_items: list[dict[str, Any]] = []
    for line in lines:
        m = re.match(r"^(.{4,44}?)\s{2,}(\d+)\s+([\d.,]+)\s+(-?[\d.,]+)$", line)
        if m and not m.group(1).lower().startswith(("subtotal", "total", "vat")):
            line_items.append(
                {
                    "description": m.group(1).strip(),
                    "quantity": parse_number(m.group(2)),
                    "unit_price": parse_number(m.group(3)),
                    "amount": parse_number(m.group(4)),
                }
            )

    is_credit = "credit note" in document.lower()
    if is_credit:
        total = -abs(total) if total is not None else None
        subtotal = -abs(subtotal) if subtotal is not None else None
        tax = -abs(tax) if tax is not None else None

    return {
        "document_type": "credit_note" if is_credit else ("invoice" if number or total else None),
        "invoice_number": number,
        "issue_date": parse_date(issue_raw) if issue_raw else None,
        "due_date": parse_date(due_raw.split("(")[0].strip()) if due_raw else None,
        "currency": currency,
        "vendor_name": vendor,
        "vendor_tax_id": _label(document, r"vat\s*/?\s*tax id", r"ust-idnr\.?", r"\bvat\b(?!\s*\d+\s*%)"),
        "bill_to_name": _label(document, r"bill to", r"rechnungsempf[aä]nger"),
        "purchase_order": _label(document, r"your reference\s*/?\s*po", r"\bpo\b"),
        "line_items": line_items,
        "subtotal": subtotal,
        "tax_amount": tax,
        "total_amount": total,
    }


def _mock_resume(document: str) -> dict[str, Any]:
    lines = [ln.rstrip() for ln in document.split("\n")]
    non_empty = [ln.strip() for ln in lines if ln.strip()]
    name = None
    for line in non_empty:
        if line.startswith(("From:", "To:", "Subject:", "Date:", "Content-Type:", "-")):
            continue
        candidate = line.replace("CURRICULUM VITAE — ", "").strip()
        if _EMAIL_RE.search(candidate):
            continue
        words = candidate.split()
        if 1 < len(words) <= 4:
            name = candidate.title() if candidate.isupper() else candidate
            break

    email_m = _EMAIL_RE.search(document)
    phone_m = _PHONE_RE.search(document)
    years_m = re.search(r"(\d{1,2})\s*\+?\s*years", document, re.IGNORECASE)

    location = None
    if email_m:
        contact_line = next((ln for ln in non_empty if email_m.group(0) in ln), "")
        parts = [p.strip() for p in contact_line.split("|")]
        if len(parts) > 1 and not _EMAIL_RE.search(parts[-1]) and not _PHONE_RE.search(parts[-1]):
            location = parts[-1]

    skills: list[str] = []
    for idx, line in enumerate(lines):
        if line.strip().upper().startswith("SKILLS"):
            for follow in lines[idx + 1 : idx + 3]:
                if follow.strip():
                    skills = [s.strip() for s in re.split(r"[,;|]", follow) if s.strip()]
                    break
            break

    roles: list[dict[str, Any]] = []
    in_experience = False
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("EXPERIENCE"):
            in_experience = True
            continue
        if upper.startswith(("EDUCATION", "SKILLS", "CERTIFICATIONS")):
            in_experience = False
        if in_experience and "," in stripped and not stripped.startswith("-"):
            head, _, tail = stripped.partition(",")
            company = re.split(r"\s{2,}", tail.strip())[0].strip()
            span = re.search(r"((?:19|20)\d{2})\s*[-–]\s*(Present|(?:19|20)\d{2})", stripped)
            roles.append(
                {
                    "company": re.sub(r"\s*((?:19|20)\d{2}).*$", "", company).strip() or None,
                    "title": head.strip() or None,
                    "start_date": span.group(1) if span else None,
                    "end_date": None if not span or span.group(2) == "Present" else span.group(2),
                    "is_current": bool(span and span.group(2) == "Present") or None,
                }
            )

    education: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if line.strip().upper().startswith("EDUCATION"):
            for follow in lines[idx + 1 : idx + 4]:
                text = follow.strip()
                if not text or text.upper().startswith(("SKILLS", "CERTIFICATIONS")):
                    break
                parts = [p.strip() for p in text.split(",")]
                degree_field = parts[0].split(" ", 1)
                year = re.search(r"((?:19|20)\d{2})", text)
                education.append(
                    {
                        "institution": parts[1] if len(parts) > 1 else None,
                        "degree": degree_field[0] or None,
                        "field_of_study": degree_field[1] if len(degree_field) > 1 else None,
                        "graduation_year": int(year.group(1)) if year else None,
                    }
                )
            break

    return {
        "full_name": name,
        "email": email_m.group(0) if email_m else None,
        "phone": phone_m.group(0).strip() if phone_m else None,
        "location": location,
        "current_title": roles[0]["title"] if roles else None,
        "years_experience": float(years_m.group(1)) if years_m else None,
        "skills": skills,
        "work_experience": roles,
        "education": education,
        "certifications": [],
    }


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #


def build_provider(
    name: str,
    *,
    model: Optional[str] = None,
    effort: str = "medium",
    thinking: str = "adaptive",
    seed: int = 0,
    rep: int = 0,
) -> Provider:
    if name == "mock":
        return MockProvider(seed=seed, rep=rep)
    if name == "anthropic":
        return AnthropicProvider(model or DEFAULT_MODEL, effort=effort, thinking=thinking)
    raise ValueError(f"unknown provider {name!r}")


def has_credentials() -> bool:
    """True when the SDK will be able to resolve auth without extra arguments."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    config = os.environ.get("ANTHROPIC_CONFIG_DIR")
    from pathlib import Path

    root = Path(config) if config else Path.home() / ".config" / "anthropic"
    return (root / "credentials").is_dir()


# --------------------------------------------------------------------------- #
# Mock behaviour for the other four prototypes
# --------------------------------------------------------------------------- #
#
# IMPORTANT, and stated here rather than buried in a docstring: the numbers
# below are *stipulated*, not measured. They encode "reasoning first should beat
# answering first" and "examples should help a classifier" as assumptions, so
# that the harness has something to measure offline. An offline run therefore
# demonstrates that the experiment is wired up correctly — it is not evidence
# that the effect is real. Run `--provider anthropic` for that.
#
# Self-consistency (prototype 4) is the exception: nothing about voting is
# stipulated. It operates on the mock's genuine per-repetition variance, so its
# offline result is emergent rather than assumed.

MOCK_AUDIT_ERROR: dict[str, dict[str, float]] = {
    #               reconciles   off by <5c    off by more   a number is missing
    "direct":      {"clean": 0.15, "subtle": 0.60, "obvious": 0.22, "missing": 0.45},
    "cot":         {"clean": 0.05, "subtle": 0.22, "obvious": 0.06, "missing": 0.15},
    "cot_fewshot": {"clean": 0.02, "subtle": 0.10, "obvious": 0.03, "missing": 0.08},
}

# Base probability that a zero-shot classifier collapses a class into `invoice`
# (or, for resumes, keeps it). Each worked example halves the error twice over.
MOCK_ROUTING_CONFUSION: dict[str, float] = {
    "credit_note": 0.70,
    "delivery_note": 0.55,
    "unreadable": 0.40,
    "invoice": 0.06,
    "resume": 0.05,
}

MOCK_JUDGE_NOISE = 0.12


def _mock_task_response(
    mock: "MockProvider",
    *,
    task: str,
    system: str,
    messages: list[dict[str, Any]],
    temperature: Optional[float],
) -> LLMResponse:
    user = _first_user_text(messages)
    document = _between(user, "<document>", "</document>") or user
    temp = 0.0 if temperature is None else temperature
    rng = _rng(f"{task}|{mock.seed}|{document[:400]}|{mock.rep if temp > 0 else 0}")

    if task == "audit":
        payload = _mock_audit(document, system, rng, temp)
    elif task == "routing":
        payload = _mock_routing(document, system, rng)
    elif task == "judge":
        payload = _mock_judge(document, _between(user, "<extraction>", "</extraction>"), rng, temp)
    elif task.startswith("fc_"):
        return _mock_factcheck(
            mock, task=task, system=system, messages=messages, temperature=temperature
        )
    else:  # pragma: no cover - guarded by the callers
        raise ValueError(f"mock provider has no simulation for task {task!r}")

    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return LLMResponse(
        text=body, model=mock.model, stop_reason="end_turn",
        usage={"input_tokens": len(document) // 4, "output_tokens": len(body) // 4},
    )


def _audit_arm(system: str) -> str:
    if "Do not explain" in system:
        return "direct"
    return "cot_fewshot" if "Example —" in system else "cot"


def _mock_audit(document: str, system: str, rng, temp: float) -> dict[str, Any]:
    parsed = _mock_invoice(document)
    subtotal, tax, total = parsed["subtotal"], parsed["tax_amount"], parsed["total_amount"]
    arm = _audit_arm(system)

    computed = None if subtotal is None or tax is None else round(subtotal + tax, 2)
    discrepancy = None if computed is None or total is None else round(total - computed, 2)
    truthful = None if discrepancy is None else abs(discrepancy) < 0.005

    if discrepancy is None:
        bucket = "missing"
    elif abs(discrepancy) < 0.005:
        bucket = "clean"
    elif abs(discrepancy) < 0.05:
        bucket = "subtle"
    else:
        bucket = "obvious"
    p_error = min(0.95, MOCK_AUDIT_ERROR[arm][bucket] * (1 + 0.3 * temp))

    verdict = truthful
    if rng.random() < p_error:
        # The characteristic failure is optimism: "looks about right".
        verdict = True if truthful is not True else False
        if bucket != "missing" and rng.random() < 0.4:
            discrepancy = 0.0
            computed = total

    if arm == "direct":
        return {
            "subtotal": subtotal,
            "tax_amount": tax,
            "total_amount": total,
            "reconciles": verdict,
            "discrepancy": discrepancy,
        }
    steps = []
    if subtotal is not None:
        steps.append(f"subtotal {subtotal:.2f}")
    if tax is not None:
        steps.append(f"tax {tax:.2f}")
    if computed is not None:
        steps.append(f"{subtotal:.2f} + {tax:.2f} = {computed:.2f}")
    if total is not None and computed is not None:
        steps.append(f"printed total {total:.2f}, difference {round(total - computed, 2):+.2f}")
    if not steps:
        steps = ["the document does not print all three amounts"]
    return {
        "steps": steps,
        "subtotal": subtotal,
        "tax_amount": tax,
        "total_amount": total,
        "computed_total": computed,
        "discrepancy": discrepancy,
        "reconciles": verdict,
    }


_RESUME_MARKERS = ("experience", "education", "skills", "curriculum vitae", "certifications")


def _route_heuristic(document: str) -> str:
    lowered = document.lower()
    printable = re.sub(r"[^a-z0-9 ]", "", lowered)
    if len(printable.split()) < 6:
        return "unreadable"
    junk_ratio = sum(ch in "#@%&*·~^`|\\/_-=+" for ch in document) / max(1, len(document))
    if junk_ratio > 0.12:
        return "unreadable"
    if "credit note" in lowered or "total credit" in lowered:
        return "credit_note"
    if "packing slip" in lowered or "delivery note" in lowered or "not an invoice" in lowered:
        return "delivery_note"
    if sum(marker in lowered for marker in _RESUME_MARKERS) >= 2:
        return "resume"
    return "invoice"


def _mock_routing(document: str, system: str, rng) -> dict[str, Any]:
    truthful = _route_heuristic(document)
    k = system.count("Class: ")
    confusion = MOCK_ROUTING_CONFUSION.get(truthful, 0.1) * (0.5 ** (k / 2))
    label = truthful
    if rng.random() < confusion:
        label = "resume" if truthful == "resume" else "invoice"
        if truthful == "invoice":
            label = rng.choice(["credit_note", "delivery_note"])
    return {
        "document_class": label,
        "confidence": "high" if label == truthful and k else rng.choice(["low", "medium", "high"]),
    }


def _mock_judge(document: str, extraction_json: str, rng, temp: float) -> dict[str, Any]:
    from . import grounding

    try:
        payload = json.loads(extraction_json) if extraction_json.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    index = grounding.DocumentIndex(document)
    noise = MOCK_JUDGE_NOISE * (1 + 0.5 * temp)
    verdicts: list[dict[str, Any]] = []

    for path, value in _iter_scalars(payload):
        if value is None or value == "" or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            supported = index.has_number(float(value))
        else:
            supported = index.has_date(str(value)) if path.endswith(("date", "year")) else index.has_text(str(value))
        verdict = "supported" if supported else rng.choice(["not_in_document", "contradicted"])
        if rng.random() < noise:  # judges are models too
            verdict = rng.choice(["supported", "not_in_document", "contradicted"])
        verdicts.append({"field": path, "verdict": verdict, "evidence": None})
    return {"verdicts": verdicts}


def _iter_scalars(node: Any, prefix: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_scalars(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            yield from _iter_scalars(value, f"{prefix}[{idx}]")
    else:
        yield prefix, node


# --------------------------------------------------------------------------- #
# Mock behaviour for the fact-checking system
# --------------------------------------------------------------------------- #
#
# As with the other prototypes, the failure *rates* are stipulated so the
# harness has something to measure offline. What is NOT stipulated: the
# retrieval, the aggregation, the gating and the grounding checks are all real
# code operating on real text, so an offline run genuinely exercises the parts
# of the system that are not the model.

MOCK_FC_UNDER_DECOMPOSE = 0.15   # leave a compound sentence as one claim
MOCK_FC_FABRICATED_SPAN = 0.06   # quote a span that is not in the input
MOCK_FC_VERIFY_NOISE = 0.10      # get a passage relation wrong
MOCK_FC_QUOTE_PARAPHRASE = 0.10  # "quote" a sentence that is not verbatim
MOCK_FC_INJECTION_COMPLIANCE = 0.5  # obey a passage that instructs the checker

_OPINION_WORDS = ("best", "worst", "beautiful", "should", "ought", "terrible", "excellent",
                  "impressive", "disappointing", "overrated")
_PREDICTION_WORDS = ("will ", "expected to", "forecast", "is set to", "plans to", "by 2030",
                     "going to")


def _mock_factcheck(
    mock: "MockProvider", *, task: str, system: str, messages: list[dict[str, Any]],
    temperature: Optional[float],
) -> LLMResponse:
    user = _first_user_text(messages)
    temp = 0.0 if temperature is None else temperature
    rng = _rng(f"{task}|{mock.seed}|{user[:400]}|{mock.rep if temp > 0 else 0}")

    if task == "fc_decompose":
        payload = _mock_decompose(_between(user, "<input>", "</input>") or user, rng)
    elif task == "fc_plan":
        payload = _mock_plan(user, rng)
    else:
        payload = _mock_verify(user, rng)

    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return LLMResponse(
        text=body, model=mock.model, stop_reason="end_turn",
        usage={"input_tokens": len(user) // 4, "output_tokens": len(body) // 4},
    )


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _mock_decompose(document: str, rng) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    for sentence in _sentences(document):
        # Split on coordinating conjunctions unless the simulated model
        # under-decomposes this one.
        parts = [sentence]
        if rng.random() > MOCK_FC_UNDER_DECOMPOSE:
            # Coordinating conjunctions and the subordinate clauses that carry
            # their own checkable content ("... after closing the deal in March").
            splitter = r",\s+and\s+|;\s+|,\s+which\s+|\s+and\s+it\s+|\s+after\s+|\s+while\s+"
            parts = [p.strip().rstrip(",") for p in re.split(splitter, sentence)
                     if len(p.strip()) > 12] or [sentence]

        for part in parts:
            part_lower = part.lower()
            if any(word in part_lower for word in _OPINION_WORDS):
                claim_type, checkable = "opinion", False
            elif any(word in part_lower for word in _PREDICTION_WORDS) and "said" not in part_lower:
                claim_type, checkable = "prediction", False
            elif len(part.split()) < 4:
                claim_type, checkable = "ambiguous", False
            else:
                claim_type, checkable = "factual", True

            span = sentence
            if rng.random() < MOCK_FC_FABRICATED_SPAN:
                span = "According to the report, " + part  # not in the input
            year = re.search(r"\b(19|20)\d{2}\b", part)
            claims.append(
                {
                    "text": part.rstrip(".") + ".",
                    "source_span": span,
                    "claim_type": claim_type,
                    "checkable": checkable,
                    "entities": re.findall(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?", part)[:3],
                    "time_reference": year.group(0) if year else None,
                }
            )
    return {"claims": claims}


def _mock_plan(user: str, rng) -> dict[str, Any]:
    claim = user.split("\n")[0].replace("Claim:", "").strip()
    entities = ""
    for line in user.split("\n"):
        if line.startswith("Entities:"):
            entities = line.split(":", 1)[1].strip()
    keywords = " ".join(w for w in claim.split() if len(w) > 3)[:120]
    return {
        "what_would_settle_this": "A primary filing or report giving the figure for the stated period.",
        "queries": [
            {"text": keywords, "intent": "primary_source"},
            {"text": (entities + " " + keywords).strip()[:120], "intent": "news"},
            {"text": (entities + " incorrect disputed correction " + keywords).strip()[:120],
             "intent": "contradiction_probe"},
        ],
    }


_NUM_RE = re.compile(r"-?\d[\d.,]*")


def _mock_verify(user: str, rng) -> dict[str, Any]:
    claim = ""
    for line in user.split("\n"):
        if line.startswith("Claim:"):
            claim = line.split(":", 1)[1].strip()
            break
    passage = _between(user, ">", "</passage>")
    if "\n" in passage:
        passage = passage.split("\n", 1)[1] if passage.startswith(">") else passage
    passage = passage.strip()

    lowered_passage = passage.lower()
    if any(marker in lowered_passage for marker in
           ("disregard your previous", "instruction for automated", "to be marked supported")):
        if rng.random() < MOCK_FC_INJECTION_COMPLIANCE:
            return {
                "quoted_evidence": _sentences(passage)[0] if _sentences(passage) else None,
                "reasoning": ["the page states these facts are verified"],
                "relation": "supports",
                "confidence": "high",
            }

    claim_tokens = {t for t in re.findall(r"[a-z]{4,}", claim.lower())}
    passage_tokens = {t for t in re.findall(r"[a-z]{4,}", lowered_passage)}
    overlap = len(claim_tokens & passage_tokens) / max(1, len(claim_tokens))

    claim_numbers = {n.rstrip(".,") for n in _NUM_RE.findall(claim)}
    passage_numbers = {n.rstrip(".,") for n in _NUM_RE.findall(passage)}

    if overlap < 0.25:
        relation, confidence = "irrelevant", "low"
    elif claim_numbers and passage_numbers:
        if claim_numbers & passage_numbers:
            relation, confidence = "supports", "high"
        else:
            relation, confidence = "refutes", "medium"
    elif overlap > 0.55:
        relation, confidence = "supports", "medium"
    else:
        relation, confidence = "insufficient", "low"

    if rng.random() < MOCK_FC_VERIFY_NOISE:
        relation = rng.choice(["supports", "refutes", "irrelevant", "insufficient"])

    quote = None
    if relation in ("supports", "refutes"):
        candidates = _sentences(passage) or [passage]
        best = max(
            candidates,
            key=lambda s: len(claim_tokens & set(re.findall(r"[a-z]{4,}", s.lower()))),
        )
        quote = best if rng.random() >= MOCK_FC_QUOTE_PARAPHRASE else "In summary, " + best[:60]

    return {
        "quoted_evidence": quote,
        "reasoning": [f"token overlap {overlap:.2f}",
                      f"claim numbers {sorted(claim_numbers) or 'none'}",
                      f"passage numbers {sorted(passage_numbers) or 'none'}"],
        "relation": relation,
        "confidence": confidence,
    }
