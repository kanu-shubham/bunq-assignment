"""Query rewriting.

Retrieval fails on vocabulary mismatch far more often than it fails on ranking.
The user asks "how many holiday days do I get"; the document says "27 days of
paid annual leave". No amount of fusion or re-ranking recovers that — neither
index ever surfaced the document, so the reranker never saw it. Rewriting is the
only stage that can *raise the ceiling* rather than reorder what is under it.

Three strategies, in increasing cost:

``keyword``      strip question scaffolding down to content terms. Helps BM25,
                 which is diluted by "how do I", "what is the", "can someone".
``expand``       add domain synonyms and acronym expansions from a maintained
                 lexicon. This is the strategy that fixes vocabulary mismatch,
                 and the lexicon is a real artefact you own and grow — treat it
                 like code, not like a magic constant.
``decompose``    split a conjunctive question into independent sub-queries, so
                 each half gets its own shot at retrieval instead of competing
                 for the same top-k.

``ClaudeQueryRewriter`` replaces the rules with a model call and additionally
extracts metadata filters from natural language ("...in the runbooks from this
year"). It is optional; everything here runs without it.

Cost note: every variant multiplies first-stage retrieval work. Rewriting is
where a RAG system's latency budget quietly goes, which is what makes semantic
caching (next module) worth building.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable

from .textutil import normalize, tokenize

# Question scaffolding that carries no retrieval signal.
_QUESTION_PREFIXES = re.compile(
    r"^\s*(how (do|does|can|would|should) (i|we|you)|what (is|are|does|do)|"
    r"where (is|are|do|can)|when (do|does|is|are|should)|why (do|does|is|are)|"
    r"who (is|are|do|does)|can (i|we|you)|is there|are there|tell me( about)?|"
    r"please|explain)\b[\s,:]*",
    re.IGNORECASE,
)

_FILLER = frozenset(
    """
    i we you it they me my our your please kindly just really actually
    thing things stuff something anything someone anyone about regarding
    """.split()
)

_CONJUNCTION_RE = re.compile(r"\s+(?:and also|and then|and|as well as|plus|,)\s+", re.IGNORECASE)


# A maintained domain lexicon. In production this lives beside the corpus, is
# reviewed like code, and grows from real query logs — the queries that returned
# nothing are the ones that tell you what is missing.
DEFAULT_LEXICON: dict[str, tuple[str, ...]] = {
    "pto": ("annual leave", "holiday", "time off", "vacation"),
    "vacation": ("annual leave", "holiday", "paid leave"),
    "holiday": ("annual leave", "paid leave"),
    "time off": ("annual leave", "holiday", "leave"),
    "maternity": ("parental leave", "birthing parent"),
    "paternity": ("parental leave", "non-birthing parent", "partner"),
    "sick": ("sick leave", "sickness"),
    "expenses": ("reimbursement", "receipts"),
    "reimburse": ("expenses", "reimbursement"),
    "pager": ("on-call", "oncall", "paged", "rotation"),
    "oncall": ("on-call", "rotation", "pager"),
    "on call": ("on-call", "rotation", "pager"),
    "outage": ("incident", "sev1", "sev2", "downtime"),
    "downtime": ("outage", "incident", "unavailability"),
    "incident": ("sev1", "sev2", "severity", "postmortem"),
    "post mortem": ("postmortem", "incident review"),
    "double charge": ("duplicate", "debited twice", "duplicate submission"),
    "charged twice": ("duplicate debit", "duplicate submission"),
    "duplicate payment": ("duplicate debit", "idempotency", "duplicate submission"),
    "retry": ("retries", "backoff", "exponential backoff"),
    "backoff": ("retry", "jitter", "exponential"),
    "dedupe": ("deduplication", "idempotency", "dedupe key"),
    "deduplication": ("idempotency", "dedupe"),
    "db": ("database", "postgres", "postgresql"),
    "postgres": ("postgresql", "database"),
    "failover": ("switchover", "promotion", "patroni"),
    "rollback": ("roll back", "undo", "revert"),
    "roll back": ("rollback", "revert"),
    "deploy": ("deployment", "release", "rollout"),
    "release": ("deploy", "rollout", "canary"),
    "flag": ("feature flag", "kill switch", "toggle"),
    "kill switch": ("operational flag", "feature flag"),
    "secret": ("credential", "vault", "api key"),
    "credentials": ("secrets", "vault", "api key"),
    "cert": ("certificate", "tls", "mtls"),
    "certificate": ("tls", "mtls", "rotation"),
    "gdpr": ("data retention", "erasure", "privacy", "right to erasure"),
    "delete my data": ("erasure", "right to erasure", "gdpr"),
    "kyc": ("identity verification", "due diligence", "onboarding", "screening"),
    "aml": ("sanctions", "screening", "compliance"),
    "fraud": ("risk score", "scoring", "step-up"),
    "risk score": ("fraud scoring", "score band"),
    "notifications": ("push", "email", "notification gateway"),
    "push notification": ("notification gateway", "apns", "fcm"),
    "webhook": ("webhook dispatcher", "delivery", "signing"),
    "lag": ("consumer lag", "backlog", "behind"),
    "queue": ("kafka", "consumer", "topic", "partition"),
    "slow": ("latency", "p99", "duration", "timeout"),
    "latency": ("p99", "duration", "timeout", "budget"),
    "logging": ("logs", "structured logging", "observability"),
    "metrics": ("prometheus", "observability", "slo"),
    "tracing": ("opentelemetry", "traces", "observability"),
    "new joiner": ("onboarding", "first week", "new starter"),
    "new starter": ("onboarding", "new joiner"),
    "pr": ("pull request", "code review"),
    "code review": ("pull request", "approvals", "reviewer"),
    "api version": ("versioning", "deprecation", "breaking change"),
    "breaking change": ("deprecation", "versioning", "sunset"),
    "balance": ("account balance", "ledger", "projection"),
    "ledger": ("double-entry", "entries", "transfer"),
    "reconciliation": ("recon", "break", "drift"),
    "flaky": ("flaky test", "quarantine", "intermittent"),
    "tests": ("testing", "unit tests", "integration"),
}


@runtime_checkable
class QueryRewriter(Protocol):
    def rewrite(self, query: str) -> "RewriteResult": ...


@dataclass
class RewriteResult:
    original: str
    variants: list[str]
    filters: Mapping[str, object] = field(default_factory=dict)
    strategy_trace: dict[str, list[str]] = field(default_factory=dict)

    @property
    def queries(self) -> list[str]:
        """Original first, then variants, deduplicated, order preserved.

        Deduplication is on the normalised form, but the strings returned are
        the originals — a rewriter must never quietly hand back a lowercased
        version of the user's query.
        """
        seen: set[str] = set()
        out: list[str] = []
        for q in [self.original, *self.variants]:
            key = normalize(q).strip()
            if key and key not in seen:
                seen.add(key)
                out.append(q)
        return out


class NoOpRewriter:
    name = "none"

    def rewrite(self, query: str) -> RewriteResult:
        return RewriteResult(original=query, variants=[])


class RuleBasedRewriter:
    """Deterministic, offline, and fast enough to sit on every request."""

    name = "rule-based"

    def __init__(
        self,
        lexicon: Mapping[str, Sequence[str]] | None = None,
        *,
        strategies: Sequence[str] = ("keyword", "expand", "decompose"),
        max_variants: int = 4,
        max_expansions: int = 6,
    ) -> None:
        self.lexicon = {normalize(k): tuple(v) for k, v in (lexicon or DEFAULT_LEXICON).items()}
        self.strategies = tuple(strategies)
        self.max_variants = max_variants
        self.max_expansions = max_expansions

    # ------------------------------------------------------------ strategies

    def _keyword(self, query: str) -> str | None:
        stripped = _QUESTION_PREFIXES.sub("", query).strip(" ?.!,")
        tokens = [t for t in tokenize(stripped) if t not in _FILLER]
        if not tokens:
            return None
        # Only worth issuing if it actually removed content terms. Re-emitting
        # the same tokens in a different order is a second identical retrieval
        # pass — pure cost, and it dilutes the fused ranking.
        if set(tokens) == set(tokenize(query)):
            return None
        return " ".join(tokens)

    def _expand(self, query: str) -> str | None:
        text = normalize(query)
        additions: list[str] = []
        # Multi-word lexicon keys first, so "time off" beats "time".
        for key in sorted(self.lexicon, key=len, reverse=True):
            if len(additions) >= self.max_expansions:
                break
            if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text):
                for syn in self.lexicon[key]:
                    if len(additions) >= self.max_expansions:
                        break
                    if normalize(syn) not in text and syn not in additions:
                        additions.append(syn)
        if not additions:
            return None
        return f"{query} {' '.join(additions)}"

    def _decompose(self, query: str) -> list[str]:
        parts = [p.strip(" ?.!,") for p in _CONJUNCTION_RE.split(query)]
        parts = [p for p in parts if len(tokenize(p)) >= 2]
        # Only worth it if the split actually produced independent questions.
        return parts if len(parts) >= 2 else []

    # ---------------------------------------------------------------- rewrite

    def rewrite(self, query: str) -> RewriteResult:
        variants: list[str] = []
        trace: dict[str, list[str]] = {}

        if "keyword" in self.strategies:
            kw = self._keyword(query)
            if kw:
                variants.append(kw)
                trace["keyword"] = [kw]

        if "expand" in self.strategies:
            expanded = self._expand(query)
            if expanded:
                variants.append(expanded)
                trace["expand"] = [expanded]
            # Expanding the keyword form too is cheap and often the best variant:
            # question scaffolding removed *and* vocabulary bridged.
            if "keyword" in trace:
                kw_expanded = self._expand(trace["keyword"][0])
                if kw_expanded:
                    variants.append(kw_expanded)
                    trace.setdefault("expand", []).append(kw_expanded)

        if "decompose" in self.strategies:
            parts = self._decompose(query)
            if parts:
                variants.extend(parts)
                trace["decompose"] = parts

        return RewriteResult(
            original=query,
            variants=variants[: self.max_variants],
            strategy_trace=trace,
        )


REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Between 2 and 4 alternative phrasings of the user's question, "
                "written the way the answer would be phrased in internal "
                "documentation. Prefer the vocabulary an engineer would use in a "
                "runbook or an ADR over the user's colloquial wording."
            ),
        },
        "filters": {
            "type": "object",
            "description": (
                "Metadata constraints stated in the question. Omit a field "
                "unless the user actually constrained it."
            ),
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["readme", "runbook", "postmortem", "adr", "policy", "guide", "standard", "glossary"],
                },
                "team": {"type": "string", "enum": ["ledger", "payments", "platform", "risk", "people"]},
                "source": {"type": "string", "enum": ["readme", "confluence", "notion"]},
                "updated_after": {"type": "string", "description": "ISO date, e.g. 2026-01-01"},
            },
            "additionalProperties": False,
        },
    },
    "required": ["queries", "filters"],
    "additionalProperties": False,
}

_REWRITE_SYSTEM = """You rewrite search queries for an internal documentation \
search system covering service READMEs, Confluence runbooks and postmortems, \
architecture decision records, and Notion policy pages for a payments platform.

Produce alternative phrasings that use the vocabulary the documentation itself \
would use. Expand acronyms, replace colloquial terms with the technical ones, \
and split a question that contains two independent asks into separate queries.

Do not answer the question. Do not invent constraints the user did not state."""


class ClaudeQueryRewriter:  # pragma: no cover - requires network + credentials
    """Model-backed rewriting via the Claude Messages API.

    Uses structured outputs so the response is a validated object rather than
    prose that has to be parsed with a regex, and caches per-query so a repeated
    question does not pay for a second call.
    """

    name = "claude"

    def __init__(
        self,
        model: str = "claude-opus-5",
        *,
        effort: str = "low",
        max_tokens: int = 2048,
        client=None,
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ImportError(
                    "ClaudeQueryRewriter needs `pip install anthropic`. "
                    "Use RuleBasedRewriter for an offline run."
                ) from exc
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                # A profile from `ant auth login` also works; the SDK resolves it.
                pass
            client = anthropic.Anthropic()
        self._client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._cache: dict[str, RewriteResult] = {}

    def rewrite(self, query: str) -> RewriteResult:
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": _REWRITE_SYSTEM,
                    # The system prompt and schema are identical on every call;
                    # caching the prefix is most of the cost of this feature.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": REWRITE_SCHEMA},
            },
            messages=[{"role": "user", "content": query}],
        )
        if response.stop_reason == "refusal":
            result = RewriteResult(original=query, variants=[])
        else:
            payload = json.loads(
                "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
            )
            result = RewriteResult(
                original=query,
                variants=[q for q in payload.get("queries", []) if q.strip()],
                filters=payload.get("filters") or {},
                strategy_trace={"claude": list(payload.get("queries", []))},
            )
        self._cache[query] = result
        return result
