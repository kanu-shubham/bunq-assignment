"""Scoring against the corpus ground truth, plus run-to-run agreement.

Five outcomes per field, and keeping them apart is the whole value of the
scorer:

    correct        truth and prediction agree on a value
    correct_null   truth is absent and the model said so — the anti-hallucination win
    hallucination  truth is absent and the model produced something anyway
    omission       truth has a value and the model returned null
    wrong          both present, different

Aggregating those into one "accuracy" number hides the failure this session is
about: a prompt change that trades omissions for hallucinations can leave
accuracy flat while making the extractor much more dangerous.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .normalize import norm_text, parse_date, parse_number

OUTCOMES = ("correct", "correct_null", "hallucination", "omission", "wrong")

_OBJECT_LIST_FIELDS = {"line_items", "work_experience", "education"}
_STRING_LIST_FIELDS = {"skills", "certifications"}


def flatten(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Flatten an extraction into `path -> scalar` pairs.

    String lists collapse to one comparable value (order and duplicates are not
    meaningful for skills); object lists expand per index so a missing third
    work-experience entry shows up as three omissions, not one.
    """
    flat: dict[str, Any] = {}
    if not payload:
        return flat
    for key, value in payload.items():
        if key in _STRING_LIST_FIELDS:
            flat[key] = tuple(sorted(norm_text(str(v)) for v in (value or []) if str(v).strip())) or None
        elif key in _OBJECT_LIST_FIELDS:
            for idx, item in enumerate(value or []):
                if not isinstance(item, dict):
                    continue
                for sub_key, sub_value in item.items():
                    flat[f"{key}[{idx}].{sub_key}"] = sub_value
        else:
            flat[key] = value
    return flat


def _absent(value: Any) -> bool:
    return value is None or value == "" or value == () or value == []


def values_match(key: str, truth: Any, pred: Any) -> bool:
    if isinstance(truth, tuple) or isinstance(pred, tuple):
        a, b = set(truth or ()), set(pred or ())
        if not a and not b:
            return True
        return bool(a) and bool(b) and len(a & b) / len(a | b) >= 0.6

    if isinstance(truth, bool) or isinstance(pred, bool):
        return bool(truth) == bool(pred)

    if isinstance(truth, (int, float)) and not key.endswith("year"):
        pred_number = parse_number(pred)
        return pred_number is not None and abs(float(truth) - pred_number) < 0.011

    if key.endswith(("date", "_year")) or key in {"issue_date", "due_date"}:
        t, p = parse_date(str(truth)), parse_date(str(pred))
        if t and p:
            # A truth of "2025" is satisfied by "2025-03" or "2025-03-14".
            return p.startswith(t) or t.startswith(p)

    if isinstance(truth, (int, float)) or isinstance(pred, (int, float)):
        t, p = parse_number(truth), parse_number(pred)
        if t is not None and p is not None:
            return abs(t - p) < 0.011

    t_text, p_text = norm_text(str(truth)), norm_text(str(pred))
    if not t_text or not p_text:
        return t_text == p_text
    return t_text == p_text or t_text in p_text or p_text in t_text


@dataclass
class DocScore:
    doc_id: str
    kind: str
    tags: list[str]
    status: str
    outcomes: Counter = field(default_factory=Counter)
    detail: dict[str, str] = field(default_factory=dict)
    followed_injection: bool = False
    repairs_used: int = 0
    refusal_ok: bool = False

    @property
    def graded(self) -> int:
        return sum(self.outcomes.values())

    @property
    def accuracy(self) -> float:
        if not self.graded:
            return 0.0
        return (self.outcomes["correct"] + self.outcomes["correct_null"]) / self.graded


def score_document(
    *,
    doc_id: str,
    kind: str,
    tags: Iterable[str],
    truth: dict[str, Any],
    payload: Optional[dict[str, Any]],
    status: str,
    repairs_used: int = 0,
    followed_injection: bool = False,
    refusal_ok: bool = False,
) -> DocScore:
    score = DocScore(
        doc_id=doc_id, kind=kind, tags=list(tags), status=status,
        followed_injection=followed_injection, repairs_used=repairs_used, refusal_ok=refusal_ok,
    )
    truth_flat = flatten(truth)
    pred_flat = flatten(payload)

    for key in sorted(set(truth_flat) | set(pred_flat)):
        t, p = truth_flat.get(key), pred_flat.get(key)
        if _absent(t) and _absent(p):
            outcome = "correct_null"
        elif _absent(t):
            outcome = "hallucination"
        elif _absent(p):
            outcome = "omission"
        elif values_match(key, t, p):
            outcome = "correct"
        else:
            outcome = "wrong"
        score.outcomes[outcome] += 1
        score.detail[key] = outcome
    return score


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def aggregate(scores: list[DocScore], results: list[Any]) -> dict[str, Any]:
    total = len(scores)
    outcomes = Counter()
    for score in scores:
        outcomes.update(score.outcomes)

    truth_present = outcomes["correct"] + outcomes["wrong"] + outcomes["omission"]
    truth_absent = outcomes["correct_null"] + outcomes["hallucination"]
    graded = sum(outcomes.values())

    statuses = Counter(score.status for score in scores)
    injection_docs = [s for s in scores if "hard:injection" in s.tags]
    refusal_bait = [s for s in scores if s.refusal_ok]
    unjustified_refusals = [s for s in scores if s.status == "refused" and not s.refusal_ok]

    by_tag: dict[str, dict[str, Any]] = {}
    tag_groups: dict[str, list[DocScore]] = defaultdict(list)
    for score in scores:
        for tag in score.tags:
            tag_groups[tag].append(score)
    for tag, group in sorted(tag_groups.items()):
        graded_group = sum(s.graded for s in group)
        hallucinated = sum(s.outcomes["hallucination"] for s in group)
        by_tag[tag] = {
            "documents": len(group),
            "accuracy": round(sum(s.outcomes["correct"] + s.outcomes["correct_null"] for s in group)
                              / graded_group, 4) if graded_group else None,
            "hallucinations": hallucinated,
            "refused": sum(1 for s in group if s.status == "refused"),
        }

    return {
        "documents": total,
        "status": dict(statuses),
        "field_outcomes": dict(outcomes),
        "accuracy": round((outcomes["correct"] + outcomes["correct_null"]) / graded, 4) if graded else None,
        "value_accuracy": round(outcomes["correct"] / truth_present, 4) if truth_present else None,
        "hallucination_rate": round(outcomes["hallucination"] / truth_absent, 4) if truth_absent else None,
        "omission_rate": round(outcomes["omission"] / truth_present, 4) if truth_present else None,
        "valid_first_try": _ratio(sum(1 for r in results if r.valid_first_try), total),
        "clean_first_try": _ratio(sum(1 for r in results if r.clean_first_try), total),
        "repaired_to_valid": sum(1 for r in results if not r.valid_first_try and r.status == "ok"),
        "mean_repairs": round(sum(r.repairs_used for r in results) / total, 3) if total else None,
        "refusals": {
            "total": statuses.get("refused", 0),
            "on_refusal_bait": sum(1 for s in refusal_bait if s.status == "refused"),
            "refusal_bait_documents": len(refusal_bait),
            "unjustified": [s.doc_id for s in unjustified_refusals],
        },
        "injection": {
            "documents": len(injection_docs),
            "followed": sum(1 for s in injection_docs if s.followed_injection),
        },
        "format_drift": dict(Counter(
            action for r in results for a in r.attempts for action in a.salvage_actions
        )),
        "by_tag": by_tag,
    }


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


# --------------------------------------------------------------------------- #
# Run-to-run agreement (the temperature sweep's payload)
# --------------------------------------------------------------------------- #


def agreement(payloads: list[Optional[dict[str, Any]]]) -> dict[str, Any]:
    """How much do N extractions of the *same* document disagree?

    `field_agreement` is the fraction of fields on which every repetition
    produced the same value; `unstable_fields` names the ones that moved.
    `identical_outputs` counts distinct whole objects — the blunt version of
    the same question.
    """
    if len(payloads) < 2:
        return {"repetitions": len(payloads), "field_agreement": 1.0, "unstable_fields": [],
                "distinct_outputs": len(payloads)}

    flats = [flatten(p) for p in payloads]
    keys = sorted({k for flat in flats for k in flat})
    unstable: list[str] = []
    for key in keys:
        first = flats[0].get(key)
        if not all(_same(first, flat.get(key), key) for flat in flats[1:]):
            unstable.append(key)

    distinct = {_canonical(p) for p in payloads}
    return {
        "repetitions": len(payloads),
        "fields": len(keys),
        "field_agreement": round(1 - len(unstable) / len(keys), 4) if keys else 1.0,
        "unstable_fields": unstable,
        "distinct_outputs": len(distinct),
    }


def _same(a: Any, b: Any, key: str) -> bool:
    if _absent(a) and _absent(b):
        return True
    if _absent(a) or _absent(b):
        return False
    return values_match(key, a, b)


def _canonical(payload: Optional[dict[str, Any]]) -> str:
    import json

    return json.dumps(payload, sort_keys=True, default=str)
