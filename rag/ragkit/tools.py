"""Tool definitions and schema design.

A tool schema is a contract *and* a prompt. The model has nothing else to go on
when it decides whether to call your tool and what to put in the arguments, so
the description is not documentation you write afterwards — it is the most
load-bearing string in the system.

Rules encoded in ``lint_tool`` below, learned the expensive way:

* **Say when to call it, not just what it does.** "Searches documentation" tells
  the model nothing about whether *this* question warrants a search. "Call this
  whenever the answer depends on internal documentation the conversation does
  not already contain" does.
* **Say when *not* to call it.** The absence of a negative boundary is why tools
  over-trigger.
* **Describe every parameter.** An undescribed parameter gets filled with a
  plausible guess.
* **Constrain with enums.** A free-text ``doc_type`` invites ``"runbooks"``,
  ``"Runbook"``, and ``"run book"``. An enum makes the wrong value impossible.
* **Turn on strict mode.** ``strict: true`` with ``additionalProperties: false``
  and an explicit ``required`` list means arguments validate exactly, which
  removes a whole class of runtime defensiveness.
* **Keep the surface small and non-overlapping.** Two tools whose descriptions
  could both plausibly apply is a coin flip on every call.

Nothing here needs an API key: the registry validates and executes locally, and
``to_anthropic_tools()`` emits the wire format when you do want to hand it to a
model.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

JsonSchema = dict[str, Any]

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


class ToolInputError(ValueError):
    """Raised when tool arguments do not satisfy the declared schema."""


class ToolExecutionError(RuntimeError):
    """Raised when a tool handler fails. Retryable failures set ``retryable``."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def validate_against_schema(value: Any, schema: JsonSchema, *, path: str = "$") -> None:
    """Validate against the JSON Schema subset the Messages API supports.

    Deliberately small: types, ``required``, ``enum``, ``additionalProperties``,
    ``items``, and numeric bounds. A tool schema that needs more than this is a
    tool that is doing too much.
    """
    expected = schema.get("type")
    if expected:
        types = _TYPE_MAP.get(expected)
        if types is None:
            raise ToolInputError(f"{path}: unsupported schema type {expected!r}")
        # bool is a subclass of int; do not let True satisfy "integer".
        if expected in {"integer", "number"} and isinstance(value, bool):
            raise ToolInputError(f"{path}: expected {expected}, got boolean")
        if not isinstance(value, types):
            raise ToolInputError(f"{path}: expected {expected}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise ToolInputError(f"{path}: {value!r} is not one of {schema['enum']}")

    if expected == "object":
        props: Mapping[str, JsonSchema] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ToolInputError(f"{path}: missing required property {key!r}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                raise ToolInputError(f"{path}: unexpected properties {sorted(extra)}")
        for key, sub in props.items():
            if key in value:
                validate_against_schema(value[key], sub, path=f"{path}.{key}")

    if expected == "array":
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                validate_against_schema(item, item_schema, path=f"{path}[{i}]")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ToolInputError(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ToolInputError(f"{path}: expected at most {schema['maxItems']} items")

    if expected in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolInputError(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolInputError(f"{path}: {value} is above maximum {schema['maximum']}")


@dataclass
class ToolSpec:
    """A tool: its contract, its handler, and its execution characteristics."""

    name: str
    description: str
    input_schema: JsonSchema
    handler: Callable[..., Any]
    parallel_safe: bool = True
    side_effecting: bool = False
    timeout_seconds: float = 5.0
    max_retries: int = 1
    strict: bool = True

    def __post_init__(self) -> None:
        if self.side_effecting and self.parallel_safe:
            # Not a hard error, but the default is wrong often enough to fix here.
            self.parallel_safe = False

    def to_anthropic(self) -> dict[str, Any]:
        """Wire format for the Messages API ``tools`` array."""
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        if self.strict:
            payload["strict"] = True
        return payload

    def validate(self, arguments: Mapping[str, Any]) -> None:
        validate_against_schema(dict(arguments), self.input_schema)

    def call(self, arguments: Mapping[str, Any]) -> Any:
        self.validate(arguments)
        return self.handler(**arguments)


@dataclass
class LintFinding:
    tool: str
    rule: str
    message: str
    severity: str = "warning"  # warning | error


def lint_tool(spec: ToolSpec) -> list[LintFinding]:
    """Static checks on a tool definition. Run these in CI, not in review."""
    findings: list[LintFinding] = []

    def add(rule: str, message: str, severity: str = "warning") -> None:
        findings.append(LintFinding(spec.name, rule, message, severity))

    if not spec.name.replace("_", "").isalnum() or spec.name != spec.name.lower():
        add("naming", "use lower_snake_case; the name is part of the prompt", "error")

    desc = spec.description.strip()
    if len(desc.split()) < 25:
        add("description-length", "under ~25 words rarely carries when-to-use guidance")
    lowered = desc.lower()
    if not any(marker in lowered for marker in ("call this when", "use this when", "use when")):
        add("when-to-use", "no explicit trigger condition — the model will guess")
    if not any(marker in lowered for marker in ("do not", "don't", "never", "instead of")):
        add("when-not-to-use", "no negative boundary — expect over-triggering")

    schema = spec.input_schema
    if schema.get("type") != "object":
        add("schema-root", "input_schema must be an object", "error")
    props: Mapping[str, JsonSchema] = schema.get("properties", {})
    if not props:
        add("schema-empty", "no parameters declared", "error")
    for key, sub in props.items():
        if not sub.get("description"):
            add("param-description", f"parameter {key!r} has no description")
        if sub.get("type") == "string" and "enum" not in sub and key.endswith(("_type", "_kind", "_status")):
            add("param-enum", f"parameter {key!r} looks categorical — consider an enum")
    if "required" not in schema:
        add("schema-required", "no `required` list; every field reads as optional")
    if spec.strict and schema.get("additionalProperties") is not False:
        add("strict-additional", "strict mode needs additionalProperties: false", "error")

    if spec.side_effecting and spec.parallel_safe:
        add("parallel-safety", "side-effecting tools must not be marked parallel-safe", "error")
    return findings


class ToolRegistry:
    """Holds tools, emits the wire format, dispatches calls."""

    def __init__(self, tools: Sequence[ToolSpec] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolSpec) -> ToolSpec:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        sig = inspect.signature(tool.handler)
        declared = set(tool.input_schema.get("properties", {}))
        accepted = {
            p.name
            for p in sig.parameters.values()
            if p.kind in {p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY}
        }
        has_kwargs = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
        if not has_kwargs and not declared.issubset(accepted):
            raise ValueError(
                f"{tool.name}: schema declares {sorted(declared - accepted)} "
                f"which the handler does not accept"
            )
        self._tools[tool.name] = tool
        return tool

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolInputError(
                f"unknown tool {name!r}; available: {sorted(self._tools)}"
            ) from None

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        return [t.to_anthropic() for t in self._tools.values()]

    def lint(self) -> list[LintFinding]:
        findings: list[LintFinding] = []
        for tool in self._tools.values():
            findings.extend(lint_tool(tool))
        names = list(self._tools)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if a in b or b in a:
                    findings.append(
                        LintFinding(
                            f"{a}/{b}",
                            "overlapping-names",
                            "names overlap; the model will have to guess between them",
                        )
                    )
        return findings


# --------------------------------------------------------------------------
# The tool surface for this RAG system.
#
# Four tools, non-overlapping, each with a trigger condition and a boundary.
# Note that `search_docs` and `fetch_document` are separate: search returns
# short passages so many results fit in context, and fetch pulls the whole
# document only once the model knows which one it wants. Collapsing them into
# one "search that returns full documents" is how a context window gets eaten.
# --------------------------------------------------------------------------

SEARCH_DOCS_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "The search query, phrased the way the documentation would phrase "
                "it rather than the way the user asked. Expand acronyms."
            ),
        },
        "top_k": {
            "type": "integer",
            "description": "How many passages to return. Default 5; use up to 12 for broad questions.",
            "minimum": 1,
            "maximum": 20,
        },
        "doc_type": {
            "type": "string",
            "description": (
                "Restrict to one kind of document. Only set this when the user asked "
                "for a specific kind; an unnecessary restriction hides the answer."
            ),
            "enum": ["readme", "runbook", "postmortem", "adr", "policy", "guide", "standard", "glossary"],
        },
        "team": {
            "type": "string",
            "description": "Restrict to one owning team. Same caution as doc_type.",
            "enum": ["ledger", "payments", "platform", "risk", "people"],
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

FETCH_DOCUMENT_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "doc_id": {
            "type": "string",
            "description": "The document id exactly as returned by search_docs, e.g. 'adr-0012-idempotency-keys'.",
        },
    },
    "required": ["doc_id"],
    "additionalProperties": False,
}

GLOSSARY_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "term": {
            "type": "string",
            "description": "The internal term or acronym to define, e.g. 'brownout', 'SEV', 'projection'.",
        },
    },
    "required": ["term"],
    "additionalProperties": False,
}

LIST_DOCS_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "doc_type": {
            "type": "string",
            "description": "Only list documents of this kind.",
            "enum": ["readme", "runbook", "postmortem", "adr", "policy", "guide", "standard", "glossary"],
        },
        "team": {
            "type": "string",
            "description": "Only list documents owned by this team.",
            "enum": ["ledger", "payments", "platform", "risk", "people"],
        },
    },
    "required": [],
    "additionalProperties": False,
}

SEARCH_DOCS_DESCRIPTION = (
    "Search the internal engineering documentation — service READMEs, Confluence "
    "runbooks and postmortems, architecture decision records, and Notion policy "
    "pages — and return the most relevant passages with their document ids. "
    "Call this whenever answering depends on internal specifics the conversation "
    "does not already contain: a service's behaviour, an error code, a policy "
    "number, an incident, or a decision and its rationale. Prefer calling it "
    "several times with different phrasings over calling it once with a long "
    "question. Do not call it for general programming knowledge, for anything the "
    "user has already told you in this conversation, or to re-fetch a passage you "
    "have already retrieved."
)

FETCH_DOCUMENT_DESCRIPTION = (
    "Retrieve the full text of one document by its id. Call this after "
    "search_docs when the returned passages are clearly from the right document "
    "but are truncated, or when the user asks about a document as a whole ('what "
    "does ADR-0012 say'). Do not call it to browse: without a specific id from a "
    "prior search this wastes a turn and floods the context with a document that "
    "may be irrelevant."
)

GLOSSARY_DESCRIPTION = (
    "Look up the definition of an internal term or acronym in the company "
    "glossary. Call this when a term in the user's question or in a retrieved "
    "passage is company-specific jargon whose meaning changes the answer — "
    "'break', 'brownout', 'step-up', 'projection'. Do not call it for standard "
    "industry terms, and do not call it before search_docs; the glossary defines "
    "terms, it does not answer questions."
)

LIST_DOCS_DESCRIPTION = (
    "List the documents available, optionally narrowed by kind or owning team, "
    "returning ids and titles only. Call this when the user asks what "
    "documentation exists, or when a search has failed twice and you need to see "
    "whether the topic is covered at all. Do not use it as a substitute for "
    "search_docs — it does not read document contents and cannot answer a "
    "question about what a document says."
)


def build_registry(retriever_fn, document_lookup, glossary_lookup, doc_index) -> ToolRegistry:
    """Wire the tool surface to a live pipeline.

    ``retriever_fn(query, top_k, doc_type, team)`` -> list of result dicts
    ``document_lookup(doc_id)`` -> document dict
    ``glossary_lookup(term)`` -> definition string or None
    ``doc_index(doc_type, team)`` -> list of {doc_id, title} dicts
    """
    return ToolRegistry(
        [
            ToolSpec(
                name="search_docs",
                description=SEARCH_DOCS_DESCRIPTION,
                input_schema=SEARCH_DOCS_SCHEMA,
                handler=retriever_fn,
                parallel_safe=True,
                timeout_seconds=3.0,
            ),
            ToolSpec(
                name="fetch_document",
                description=FETCH_DOCUMENT_DESCRIPTION,
                input_schema=FETCH_DOCUMENT_SCHEMA,
                handler=document_lookup,
                parallel_safe=True,
                timeout_seconds=2.0,
            ),
            ToolSpec(
                name="define_term",
                description=GLOSSARY_DESCRIPTION,
                input_schema=GLOSSARY_SCHEMA,
                handler=glossary_lookup,
                parallel_safe=True,
                timeout_seconds=1.0,
            ),
            ToolSpec(
                name="list_documents",
                description=LIST_DOCS_DESCRIPTION,
                input_schema=LIST_DOCS_SCHEMA,
                handler=doc_index,
                parallel_safe=True,
                timeout_seconds=1.0,
            ),
        ]
    )
