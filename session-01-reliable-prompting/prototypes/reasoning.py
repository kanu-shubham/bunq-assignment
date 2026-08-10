"""Prototype 2 — chain-of-thought vs answering first.

The task: read the printed subtotal, tax and total off an invoice and say
whether the total reconciles. It is deliberately a *two-step* task — read three
numbers, then do arithmetic on them — because that is the shape where reasoning
before answering can help and single-step extraction is not.

Three arms:

    direct       answer-only schema. Nowhere to put the working.
    cot          the working is part of the schema, declared *before* the verdict
    cot_fewshot  the same, plus two worked examples

**Field order is the whole trick with structured outputs.** Generation follows
schema order, so `steps` and `computed_total` must be declared before
`reconciles`. Put the verdict first and you do not get chain-of-thought — you
get a rationalisation of an answer the model already committed to. `CoTAudit` in
`schemas.py` is ordered accordingly, and the ordering has a test.

Ground truth needs no new labels: the corpus records what each invoice *prints*,
so `subtotal + tax == total` over the truth values *is* the answer. Three
buckets, and they behave very differently:

    obvious   the printed total is off by a visible amount
    subtotal  off by one or two cents — a rounding error, where guessing "yes"
              is right often enough to be tempting
    missing   the document does not print all three numbers, so the honest
              answer is null and the failure mode is confabulating a verdict

On a thinking model, adaptive thinking already does much of what the `cot` arm
asks for in the prompt, so the interesting comparison there is `--thinking
disabled` (where the schema is the only place reasoning can live) against
adaptive. Both are available via the CLI flags.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from extraction import prompts
from extraction.pipeline import ExtractConfig, run_structured
from extraction.schemas import CoTAudit, DirectAudit

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"

ARMS = ("direct", "cot", "cot_fewshot")


def truth_verdict(truth: dict[str, Any]) -> tuple[Optional[bool], str]:
    """The reconciliation answer and its difficulty bucket, from the corpus truth."""
    subtotal, tax, total = truth.get("subtotal"), truth.get("tax_amount"), truth.get("total_amount")
    if subtotal is None or tax is None or total is None:
        return None, "missing"
    discrepancy = round(total - (subtotal + tax), 2)
    if abs(discrepancy) < 0.005:
        return True, "reconciles"
    return False, "subtle" if abs(discrepancy) < 0.05 else "obvious"


def run(args: argparse.Namespace) -> int:
    from extraction.cli import _load, _provider, _require_credentials

    _require_credentials(args)
    docs = [d for d in _load(args) if d.kind == "invoice"]
    if not docs:
        print("no invoices in the corpus selection", file=sys.stderr)
        return 2

    labels = {d.doc_id: truth_verdict(d.truth) for d in docs}
    arms: list[dict[str, Any]] = []

    for arm in ARMS:
        model_cls = DirectAudit if arm == "direct" else CoTAudit
        print(f"\n=== {arm} · {len(docs)} invoices ===", file=sys.stderr)
        config = ExtractConfig(
            prompt=arm, output_mode=args.output_mode, max_attempts=args.max_attempts,
            repair_ungrounded=False, temperature=args.temperature,
        )

        def one(doc, arm=arm, model_cls=model_cls, config=config):
            return run_structured(
                item_id=doc.doc_id,
                model_cls=model_cls,
                system=prompts.AUDIT_PROMPTS[arm],
                user=prompts.audit_user_prompt(doc.text),
                provider=_provider(args),
                config=config,
                noun="audit",
                task="audit",
            )

        results = _run_batch_custom(docs, one, args.concurrency)
        arms.append(_summarise(arm, docs, results, labels))

    report = {"meta": _meta(args, len(docs)), "arms": arms}
    out = RUNS_DIR / "reasoning"
    out.mkdir(parents=True, exist_ok=True)
    (out / "reasoning.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = render(report)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"artefacts: {out}", file=sys.stderr)
    return 0


def _run_batch_custom(docs, fn, concurrency: int):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return list(pool.map(fn, docs))


def _meta(args: argparse.Namespace, n: int) -> dict[str, Any]:
    return {
        "provider": args.provider,
        "model": args.model if args.provider == "anthropic" else "mock-1",
        "documents": n,
        "output_mode": args.output_mode,
        "thinking": args.thinking,
        "effort": args.effort,
    }


def _summarise(arm: str, docs, results, labels) -> dict[str, Any]:
    by_bucket: dict[str, Counter] = {}
    correct = 0
    graded = 0
    false_ok = 0  # said it reconciles when it does not — the expensive direction
    false_alarm = 0  # said it does not when it does
    invented_verdict = 0  # gave a verdict where the honest answer is null
    mistakes: list[dict[str, Any]] = []

    by_id = {d.doc_id: d for d in docs}
    for result in results:
        expected, bucket = labels[result.doc_id]
        payload = result.payload or {}
        got = payload.get("reconciles")
        counter = by_bucket.setdefault(bucket, Counter())
        graded += 1
        counter["n"] += 1

        if got == expected:
            correct += 1
            counter["correct"] += 1
        else:
            counter["wrong"] += 1
            if expected is None and got is not None:
                invented_verdict += 1
            elif got is True and expected is False:
                false_ok += 1
            elif got is False and expected is True:
                false_alarm += 1
            mistakes.append(
                {
                    "doc_id": result.doc_id,
                    "bucket": bucket,
                    "expected": expected,
                    "got": got,
                    "discrepancy": payload.get("discrepancy"),
                    "tags": by_id[result.doc_id].tags,
                }
            )

    return {
        "arm": arm,
        "documents": graded,
        "accuracy": round(correct / graded, 4) if graded else None,
        "false_ok": false_ok,
        "false_alarm": false_alarm,
        "invented_verdict": invented_verdict,
        "valid_first_try": round(sum(1 for r in results if r.valid_first_try) / graded, 4) if graded else None,
        "mean_repairs": round(sum(r.repairs_used for r in results) / graded, 3) if graded else None,
        "by_bucket": {
            bucket: {
                "n": counts["n"],
                "accuracy": round(counts["correct"] / counts["n"], 4) if counts["n"] else None,
            }
            for bucket, counts in sorted(by_bucket.items())
        },
        "mistakes": mistakes[:10],
    }


def render(report: dict[str, Any]) -> str:
    meta = report["meta"]
    buckets = sorted({b for arm in report["arms"] for b in arm["by_bucket"]})
    lines = [
        "# Prototype 2 — chain-of-thought vs direct answer",
        "",
        f"- provider: `{meta['provider']}` · model: `{meta['model']}` · thinking: `{meta['thinking']}`"
        f" · effort: `{meta['effort']}`",
        f"- task: does the printed total equal subtotal + tax? · {meta['documents']} invoices",
        "",
        "`false OK` is the expensive error — the audit passed an invoice whose arithmetic",
        "does not add up. `invented verdict` is a true/false answer on a document that does",
        "not print all three numbers, where the honest answer is null.",
        "",
        "| arm | accuracy | false OK | false alarm | invented verdict | valid 1st try |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for arm in report["arms"]:
        acc = "—" if arm["accuracy"] is None else f"{arm['accuracy'] * 100:.1f}%"
        vft = "—" if arm["valid_first_try"] is None else f"{arm['valid_first_try'] * 100:.1f}%"
        lines.append(
            f"| `{arm['arm']}` | {acc} | {arm['false_ok']} | {arm['false_alarm']} | "
            f"{arm['invented_verdict']} | {vft} |"
        )

    lines += ["", "## Accuracy by difficulty", "", "| arm | " + " | ".join(buckets) + " |",
              "| --- |" + " --- |" * len(buckets)]
    for arm in report["arms"]:
        cells = []
        for bucket in buckets:
            stats = arm["by_bucket"].get(bucket)
            cells.append("—" if not stats or stats["accuracy"] is None
                         else f"{stats['accuracy'] * 100:.0f}% (n={stats['n']})")
        lines.append(f"| `{arm['arm']}` | " + " | ".join(cells) + " |")

    lines += ["", "## Sample mistakes", ""]
    for arm in report["arms"]:
        if not arm["mistakes"]:
            lines.append(f"- **{arm['arm']}**: none")
            continue
        shown = ", ".join(
            f"`{m['doc_id']}` ({m['bucket']}: expected {m['expected']}, got {m['got']})"
            for m in arm["mistakes"][:4]
        )
        lines.append(f"- **{arm['arm']}**: {shown}")
    return "\n".join(lines) + "\n"
