"""Prototype 5 — LLM as judge, scored against ground truth.

Using a model to grade another model's output is the standard answer to "how do
I evaluate this at scale". The standard mistake is shipping the judge without
ever checking it, at which point you have replaced an unmeasured extractor with
an unmeasured extractor *and* an unmeasured judge.

So this prototype does the thing that makes a judge usable: it treats the judge
as a **binary error detector** and scores it against the corpus ground truth.

    the judge says `supported`                     -> it claims the field is fine
    `contradicted` / `not_in_document`             -> it flags a problem

    ground truth says `hallucination` / `wrong`    -> there really is a problem

From that: precision (of the fields it flagged, how many were really wrong),
recall (of the fields that were really wrong, how many it caught), and the false
alarm rate. A judge with 40% precision is not a quality gate, it is a queue of
busywork — and you cannot know which you have without this table.

Two comparisons make the numbers mean something:

*   **The judge vs the deterministic grounding checker** on the same fields.
    `grounding.py` is ~150 lines of normalisation and substring matching with no
    tokens and no latency. If it matches the judge, the judge is not earning its
    cost on this task.
*   **The judge against itself.** With `--judge-repeats > 1` the same field is
    judged several times and the flip rate is reported. A judge that disagrees
    with itself sets a ceiling on how much its verdicts can be trusted.

The judge is deliberately given the extraction *and* the document but is asked
only for verdicts — not corrections. A judge that re-extracts is just a second
extractor, and its agreement with the first tells you nothing about either.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from extraction import grounding, prompts, scoring
from extraction.pipeline import ExtractConfig, run_structured
from extraction.schemas import JudgeReport

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"

FLAG_VERDICTS = frozenset({"contradicted", "not_in_document"})
REAL_PROBLEMS = frozenset({"hallucination", "wrong"})


def run(args: argparse.Namespace) -> int:
    from extraction.cli import _load, _provider, _require_credentials, _run_batch

    _require_credentials(args)
    docs = _load(args)

    # Step 1: produce something to judge.
    print(f"extracting {len(docs)} documents to judge", file=sys.stderr)
    extract_config = ExtractConfig(
        prompt=args.prompt, output_mode=args.output_mode, max_attempts=args.max_attempts,
        repair_ungrounded=False,  # leave the invented values in — they are the judge's exam
        temperature=args.temperature,
    )
    extractions = {
        r.doc_id: r
        for r in _run_batch(docs, lambda: _provider(args), extract_config,
                            concurrency=args.concurrency, progress=False)
    }

    # Step 2: judge each extraction, `--judge-repeats` times.
    by_id = {d.doc_id: d for d in docs}
    judged: dict[str, list[dict[str, str]]] = {}
    judge_failures = 0

    for rep in range(args.judge_repeats):
        print(f"  judging, pass {rep + 1}/{args.judge_repeats}", file=sys.stderr)
        for doc in docs:
            extraction = extractions.get(doc.doc_id)
            if not extraction or not extraction.payload:
                continue
            result = run_structured(
                item_id=doc.doc_id,
                model_cls=JudgeReport,
                system=prompts.JUDGE_SYSTEM,
                user=prompts.judge_user_prompt(
                    doc.text, json.dumps(extraction.payload, ensure_ascii=False, indent=2)
                ),
                provider=_provider(args, rep=rep),
                config=ExtractConfig(
                    prompt="judge", output_mode=args.output_mode,
                    max_attempts=args.max_attempts, repair_ungrounded=False,
                    temperature=args.temperature,
                ),
                noun="judge report",
                task="judge",
            )
            if not result.payload:
                judge_failures += 1
                continue
            verdicts = {
                v["field"]: v["verdict"]
                for v in result.payload.get("verdicts", [])
                if isinstance(v, dict) and v.get("field")
            }
            judged.setdefault(doc.doc_id, []).append(verdicts)

    # Step 3: score the judge against the truth, and against the cheap checker.
    confusion = Counter()
    grounding_confusion = Counter()
    flip_counts = Counter()
    examples: list[dict[str, Any]] = []

    for doc_id, passes in judged.items():
        doc = by_id[doc_id]
        payload = extractions[doc_id].payload or {}
        truth_detail = scoring.score_flat(scoring.flatten(doc.truth), scoring.flatten(payload))
        index = grounding.DocumentIndex(doc.text)
        first = passes[0]

        for field, verdict in first.items():
            outcome = truth_detail.get(_normalise_path(field))
            if outcome is None or outcome == "correct_null":
                continue  # the judge was not asked about fields that are null on both sides
            really_wrong = outcome in REAL_PROBLEMS
            flagged = verdict in FLAG_VERDICTS
            confusion[_cell(flagged, really_wrong)] += 1

            value = scoring.flatten(payload).get(_normalise_path(field))
            grounding_flag = value is not None and not _grounded(index, _normalise_path(field), value)
            grounding_confusion[_cell(grounding_flag, really_wrong)] += 1

            if len(examples) < 12 and flagged != really_wrong:
                examples.append(
                    {"doc_id": doc_id, "field": field, "judge": verdict,
                     "truth_outcome": outcome, "value": value}
                )

            if len(passes) > 1:
                seen = {p.get(field) for p in passes if field in p}
                flip_counts["fields"] += 1
                flip_counts["flipped"] += len(seen) > 1

    report = {
        "meta": {
            "provider": args.provider,
            "model": args.model if args.provider == "anthropic" else "mock-1",
            "documents": len(docs),
            "judged_documents": len(judged),
            "judge_repeats": args.judge_repeats,
            "judge_failures": judge_failures,
            "extraction_prompt": args.prompt,
        },
        "judge": _detector_scores(confusion),
        "grounding_checker": _detector_scores(grounding_confusion),
        "self_consistency": {
            "fields_judged_more_than_once": flip_counts["fields"],
            "fields_where_the_verdict_changed": flip_counts["flipped"],
            "flip_rate": round(flip_counts["flipped"] / flip_counts["fields"], 4)
            if flip_counts["fields"] else None,
        },
        "disagreements": examples,
    }

    out = RUNS_DIR / "judge"
    out.mkdir(parents=True, exist_ok=True)
    (out / "judge.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    text = render(report)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"artefacts: {out}", file=sys.stderr)
    return 0


def _normalise_path(field: str) -> str:
    """The judge may name a nested field `line_items.0.amount`; the scorer uses
    `line_items[0].amount`."""
    import re

    return re.sub(r"\.(\d+)\.", r"[\1].", field)


def _grounded(index: grounding.DocumentIndex, field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return index.has_number(float(value))
    if isinstance(value, tuple):
        return all(index.has_text(str(v)) for v in value)
    text = str(value)
    return index.has_date(text) if field.endswith(("date", "_year")) else index.has_text(text)


def _cell(flagged: bool, really_wrong: bool) -> str:
    if flagged and really_wrong:
        return "true_positive"
    if flagged and not really_wrong:
        return "false_positive"
    if not flagged and really_wrong:
        return "false_negative"
    return "true_negative"


def _detector_scores(confusion: Counter) -> dict[str, Any]:
    tp, fp, fn, tn = (confusion[k] for k in
                      ("true_positive", "false_positive", "false_negative", "true_negative"))
    return {
        "fields": tp + fp + fn + tn,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "false_alarm_rate": round(fp / (fp + tn), 4) if fp + tn else None,
    }


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render(report: dict[str, Any]) -> str:
    meta = report["meta"]
    judge, checker = report["judge"], report["grounding_checker"]
    consistency = report["self_consistency"]
    lines = [
        "# Prototype 5 — LLM as judge, scored against ground truth",
        "",
        f"- provider: `{meta['provider']}` · model: `{meta['model']}`",
        f"- {meta['judged_documents']} of {meta['documents']} extractions judged"
        f" · {meta['judge_repeats']} judging pass(es) · {meta['judge_failures']} judge call(s) failed",
        f"- extraction under test used the `{meta['extraction_prompt']}` prompt, with grounding repair"
        " switched off so invented values survive for the judge to find",
        "",
        "## The judge as an error detector",
        "",
        "| detector | precision | recall | false alarms | fields |",
        "| --- | --- | --- | --- | --- |",
        f"| LLM judge | {_pct(judge['precision'])} | {_pct(judge['recall'])} | "
        f"{_pct(judge['false_alarm_rate'])} | {judge['fields']} |",
        f"| `grounding.py` (no tokens) | {_pct(checker['precision'])} | {_pct(checker['recall'])} | "
        f"{_pct(checker['false_alarm_rate'])} | {checker['fields']} |",
        "",
        "Precision is *of the fields the detector flagged, how many were really wrong*.",
        "Recall is *of the fields that were really wrong, how many it caught*. A judge",
        "that flags everything scores perfect recall and is worthless; read them together.",
        "",
        f"Judge confusion: {judge['true_positive']} caught, {judge['false_negative']} missed, "
        f"{judge['false_positive']} false alarms, {judge['true_negative']} correctly left alone.",
        "",
        "## Does the judge agree with itself?",
        "",
        f"- fields judged more than once: {consistency['fields_judged_more_than_once']}",
        f"- fields where the verdict changed between passes: "
        f"{consistency['fields_where_the_verdict_changed']} "
        f"(**{_pct(consistency['flip_rate'])}**)",
        "",
        (
            "A flip rate of exactly zero usually means the judge ran greedily (no temperature "
            "set), in which case repeated passes are near-identical by construction and this "
            "number measures nothing. Set `--temperature` on a temperature-capable model to "
            "make it informative."
            if consistency["flip_rate"] == 0
            else ""
        ),
        "A judge that flips on its own re-run cannot be more reliable than that flip rate,",
        "whatever its precision looks like on a single pass.",
        "",
        "## Where the judge and the truth disagree",
        "",
        "| doc | field | judge said | truth says | value |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["disagreements"]:
        lines.append(
            f"| `{row['doc_id']}` | `{row['field']}` | {row['judge']} | {row['truth_outcome']} | "
            f"{str(row['value'])[:40]} |"
        )
    return "\n".join(lines) + "\n"
