"""Prototype 3 — few-shot vs zero-shot, as a k-curve.

The task: route each document to one of five queues — `invoice`, `credit_note`,
`resume`, `delivery_note`, `unreadable`. Classification rather than extraction,
because that is where few-shot prompting is easiest to measure: one label per
document, so accuracy is unambiguous and the confusion matrix says exactly which
distinction the examples taught.

Two things this prototype is careful about, both of which are easy to get wrong
and both of which invalidate the result silently:

**Exemplars are hand-written, never sampled from the corpus.** Drawing few-shot
examples from your evaluation set leaks the answers; the curve then measures
memorisation and looks wonderful. `_ROUTER_EXEMPLARS` in `prompts.py` is a
separate, hand-written set.

**The k-curve is swept, not spot-checked.** k ∈ {0, 1, 2, 4, 8} — because the
useful finding is rarely "examples help" but "examples help up to k=2 and then
you are paying tokens for nothing". The per-class recall column shows *which*
class each example bought, which is what you actually act on.

The hard classes are the minority ones. `invoice` is the majority label, so a
zero-shot classifier that collapses everything into `invoice` still scores
respectably on accuracy — read the per-class recall, not the headline.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from extraction import prompts
from extraction.pipeline import ExtractConfig, run_structured
from extraction.schemas import RoutingDecision

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"

CLASSES = ("invoice", "credit_note", "resume", "delivery_note", "unreadable")
DEFAULT_KS = (0, 1, 2, 4, 8)


def truth_class(doc) -> str:
    """The routing label, derived from the corpus tags."""
    tags = set(doc.tags)
    if "hard:illegible" in tags or "hard:empty" in tags:
        return "unreadable"
    if "hard:wrong_document_type" in tags:
        return "delivery_note"
    if "hard:credit_note" in tags:
        return "credit_note"
    return "resume" if doc.kind == "resume" else "invoice"


def run(args: argparse.Namespace) -> int:
    from extraction.cli import _load, _provider, _require_credentials

    _require_credentials(args)
    docs = _load(args)
    ks = [int(k) for k in str(args.shots).split(",") if k.strip() != ""]
    labels = {d.doc_id: truth_class(d) for d in docs}

    arms: list[dict[str, Any]] = []
    for k in ks:
        print(f"\n=== k={k} shots · {len(docs)} documents ===", file=sys.stderr)
        system = prompts.router_system_prompt(k)
        config = ExtractConfig(
            prompt=f"router-k{k}", output_mode=args.output_mode,
            max_attempts=args.max_attempts, repair_ungrounded=False, temperature=args.temperature,
        )

        def one(doc, system=system, config=config):
            return run_structured(
                item_id=doc.doc_id,
                model_cls=RoutingDecision,
                system=system,
                user=prompts.router_user_prompt(doc.text),
                provider=_provider(args),
                config=config,
                noun="routing decision",
                task="routing",
            )

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            results = list(pool.map(one, docs))
        arms.append(_summarise(k, len(system), results, labels))

    report = {
        "meta": {
            "provider": args.provider,
            "model": args.model if args.provider == "anthropic" else "mock-1",
            "documents": len(docs),
            "shots": ks,
            "classes": list(CLASSES),
        },
        "arms": arms,
    }
    out = RUNS_DIR / "routing"
    out.mkdir(parents=True, exist_ok=True)
    (out / "routing.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = render(report)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"artefacts: {out}", file=sys.stderr)
    return 0


def _summarise(k: int, prompt_chars: int, results, labels) -> dict[str, Any]:
    confusion: dict[str, Counter] = defaultdict(Counter)
    correct = 0
    for result in results:
        expected = labels[result.doc_id]
        got = (result.payload or {}).get("document_class") or "<none>"
        confusion[expected][got] += 1
        correct += got == expected

    per_class = {}
    for cls in CLASSES:
        total = sum(confusion[cls].values())
        per_class[cls] = {
            "support": total,
            "recall": round(confusion[cls][cls] / total, 4) if total else None,
            "confused_with": {k2: v for k2, v in confusion[cls].most_common() if k2 != cls},
        }

    return {
        "k": k,
        "prompt_chars": prompt_chars,
        "accuracy": round(correct / len(results), 4) if results else None,
        "valid_first_try": round(sum(1 for r in results if r.valid_first_try) / len(results), 4)
        if results else None,
        "per_class": per_class,
    }


def render(report: dict[str, Any]) -> str:
    meta = report["meta"]
    lines = [
        "# Prototype 3 — few-shot vs zero-shot (document routing)",
        "",
        f"- provider: `{meta['provider']}` · model: `{meta['model']}`",
        f"- {meta['documents']} documents into {len(meta['classes'])} queues",
        "",
        "`invoice` is the majority class, so accuracy alone flatters a classifier that",
        "collapses everything into it. The per-class recall table below is the one to read.",
        "",
        "| k (examples) | prompt size | accuracy | " + " | ".join(f"recall {c}" for c in CLASSES) + " |",
        "| --- | --- | --- |" + " --- |" * len(CLASSES),
    ]
    for arm in report["arms"]:
        cells = []
        for cls in CLASSES:
            recall = arm["per_class"][cls]["recall"]
            cells.append("—" if recall is None else f"{recall * 100:.0f}%")
        acc = "—" if arm["accuracy"] is None else f"{arm['accuracy'] * 100:.1f}%"
        lines.append(
            f"| {arm['k']} | {arm['prompt_chars']} chars | {acc} | " + " | ".join(cells) + " |"
        )

    support = report["arms"][0]["per_class"]
    lines += [
        "",
        "Support (documents per class): "
        + ", ".join(f"{cls} {support[cls]['support']}" for cls in CLASSES)
        + ". Recall on a class with two documents moves in 50-point steps — read it as "
        "a direction, not a measurement.",
    ]

    lines += ["", "## What each class gets confused with", ""]
    for arm in report["arms"]:
        parts = []
        for cls in CLASSES:
            confused = arm["per_class"][cls]["confused_with"]
            if confused:
                parts.append(f"{cls} → " + ", ".join(f"{k2} ×{v}" for k2, v in confused.items()))
        lines.append(f"- **k={arm['k']}**: " + ("; ".join(parts) if parts else "nothing misrouted"))
    return "\n".join(lines) + "\n"
