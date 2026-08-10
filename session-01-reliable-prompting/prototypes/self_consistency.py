"""Prototype 4 — self-consistency: sample n times and take the majority.

The temperature sweep establishes that the extractor disagrees with itself.
This is the direct answer to that: run the same document n times, vote per
field, and see whether the vote beats a single sample.

Two results come out, and the second is the more useful one:

1.  **Voted accuracy vs single-sample accuracy.** Voting cancels independent
    errors. It cannot fix a bias — if the model is wrong the same way every
    time, the majority is wrong too — so the gain is bounded by how much of the
    error is variance rather than bias, and measuring that split is the point.

2.  **Whether the vote margin is a usable confidence signal.** The share of
    samples backing the winning value is free — no extra prompt, no judge, no
    calibration data — and it is widely treated as a confidence score. Whether
    it *is* one depends entirely on the error mix: a margin can only flag errors
    that vary run to run, so a model that is wrong the same way every time
    produces confidently unanimous mistakes. The report computes the split and
    says so either way, because an abstain rule built on a margin that does not
    discriminate routes correct fields to a human and calls it quality control.

Unlike the other prototypes, nothing here is stipulated by the mock. Voting
operates on the mock's genuine per-repetition variance, so the offline numbers
are an emergent property of the harness rather than an assumption baked into
it — this is the one prototype whose offline result means something on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from extraction import scoring
from extraction.pipeline import ExtractConfig

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"

MARGIN_BUCKETS = ((1.0, "unanimous"), (0.75, "strong (≥75%)"), (0.5, "split (≥50%)"), (0.0, "scattered"))


def run(args: argparse.Namespace) -> int:
    from extraction.cli import _load, _provider, _require_credentials, _run_batch

    _require_credentials(args)
    docs = _load(args)
    config = ExtractConfig(
        prompt=args.prompt, output_mode=args.output_mode, max_attempts=args.max_attempts,
        repair_ungrounded=not args.no_grounding_repair, temperature=args.temperature,
    )

    samples: list[dict[str, Any]] = []
    for rep in range(args.samples):
        print(f"  sample {rep + 1}/{args.samples}", file=sys.stderr)
        results = _run_batch(
            docs, lambda rep=rep: _provider(args, rep=rep), config,
            concurrency=args.concurrency, progress=False,
        )
        samples.append({r.doc_id: r for r in results})

    single = Counter()
    voted = Counter()
    margin_stats: dict[str, Counter] = {label: Counter() for _, label in MARGIN_BUCKETS}
    abstain_threshold = args.abstain_below
    abstained = 0
    abstained_would_be_wrong = 0
    kept = Counter()
    per_doc: list[dict[str, Any]] = []

    for doc in docs:
        payloads = [samples[i][doc.doc_id].payload for i in range(args.samples) if doc.doc_id in samples[i]]
        if not payloads:
            continue
        truth_flat = scoring.flatten(doc.truth)

        single_detail = scoring.score_flat(truth_flat, scoring.flatten(payloads[0]))
        single.update(single_detail.values())

        voted_flat, margins = scoring.vote(payloads)
        voted_detail = scoring.score_flat(truth_flat, voted_flat)
        voted.update(voted_detail.values())

        for field, outcome in voted_detail.items():
            margin = margins.get(field, 1.0)
            label = _bucket(margin)
            margin_stats[label]["n"] += 1
            margin_stats[label]["correct"] += outcome in ("correct", "correct_null")

            if margin < abstain_threshold:
                abstained += 1
                abstained_would_be_wrong += outcome not in ("correct", "correct_null")
            else:
                kept[outcome] += 1

        improved = _accuracy(voted_detail) - _accuracy(single_detail)
        per_doc.append(
            {
                "doc_id": doc.doc_id,
                "tags": doc.tags,
                "single_accuracy": round(_accuracy(single_detail), 4),
                "voted_accuracy": round(_accuracy(voted_detail), 4),
                "delta": round(improved, 4),
                "min_margin": round(min(margins.values()), 3) if margins else 1.0,
            }
        )

    report = {
        "meta": {
            "provider": args.provider,
            "model": args.model if args.provider == "anthropic" else "mock-1",
            "documents": len(docs),
            "samples": args.samples,
            "prompt": args.prompt,
            "temperature": args.temperature,
            "abstain_below": abstain_threshold,
        },
        "single_sample": _rates(single),
        "majority_vote": _rates(voted),
        "by_margin": {
            label: {
                "fields": counts["n"],
                "accuracy": round(counts["correct"] / counts["n"], 4) if counts["n"] else None,
            }
            for label, counts in margin_stats.items()
        },
        "abstain_policy": {
            "threshold": abstain_threshold,
            "fields_sent_for_review": abstained,
            "of_which_would_have_been_wrong": abstained_would_be_wrong,
            "precision_of_the_abstain_rule": round(abstained_would_be_wrong / abstained, 4)
            if abstained else None,
            "accuracy_on_what_is_kept": _rates(kept)["accuracy"],
        },
        "margin_discrimination": _discrimination(margin_stats),
        "biggest_movers": sorted(per_doc, key=lambda d: -abs(d["delta"]))[:10],
    }

    out = RUNS_DIR / "self-consistency"
    out.mkdir(parents=True, exist_ok=True)
    (out / "self-consistency.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = render(report)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"artefacts: {out}", file=sys.stderr)
    return 0


def _discrimination(margin_stats: dict[str, Counter]) -> dict[str, Any]:
    """Does the vote margin actually separate right answers from wrong ones?

    It only can where the errors are *variance* — different each run. Errors the
    model makes the same way every time produce a unanimous wrong answer, and no
    amount of sampling will flag them. Reporting this honestly matters: an
    abstain rule built on a margin that does not discriminate sends random
    fields to a human and calls it quality control.
    """
    unanimous = margin_stats["unanimous"]
    contested = Counter()
    for label, counts in margin_stats.items():
        if label != "unanimous":
            contested.update(counts)

    u_acc = unanimous["correct"] / unanimous["n"] if unanimous["n"] else None
    c_acc = contested["correct"] / contested["n"] if contested["n"] else None

    if u_acc is None or c_acc is None or contested["n"] < 10:
        verdict = (
            "Too few contested fields to judge whether the margin discriminates — sample "
            "more, or raise the temperature, before trusting it as a confidence signal."
        )
    elif u_acc - c_acc > 0.05:
        verdict = (
            "The margin discriminates: unanimity is a usable confidence signal here, and "
            "an abstain rule on it will catch real errors."
        )
    else:
        verdict = (
            "The margin does **not** discriminate here — unanimous answers are no more "
            "accurate than contested ones. That means most of the remaining error is bias, "
            "not variance: the model is wrong the same way every time. Sampling more will "
            "not fix it and an abstain rule on the margin would mostly route correct fields "
            "to a human. Fix the prompt or the schema instead."
        )
    return {
        "unanimous_fields": unanimous["n"],
        "unanimous_accuracy": round(u_acc, 4) if u_acc is not None else None,
        "contested_fields": contested["n"],
        "contested_accuracy": round(c_acc, 4) if c_acc is not None else None,
        "verdict": verdict,
    }


def _bucket(margin: float) -> str:
    for threshold, label in MARGIN_BUCKETS:
        if margin >= threshold:
            return label
    return MARGIN_BUCKETS[-1][1]


def _accuracy(detail: dict[str, str]) -> float:
    if not detail:
        return 0.0
    good = sum(1 for outcome in detail.values() if outcome in ("correct", "correct_null"))
    return good / len(detail)


def _rates(counts: Counter) -> dict[str, Any]:
    total = sum(counts.values())
    truth_absent = counts["correct_null"] + counts["hallucination"]
    truth_present = counts["correct"] + counts["wrong"] + counts["omission"]
    return {
        "fields": total,
        "accuracy": round((counts["correct"] + counts["correct_null"]) / total, 4) if total else None,
        "hallucination_rate": round(counts["hallucination"] / truth_absent, 4) if truth_absent else None,
        "omission_rate": round(counts["omission"] / truth_present, 4) if truth_present else None,
        "outcomes": dict(counts),
    }


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render(report: dict[str, Any]) -> str:
    meta = report["meta"]
    single, voted = report["single_sample"], report["majority_vote"]
    abstain = report["abstain_policy"]
    lines = [
        "# Prototype 4 — self-consistency (majority vote over n samples)",
        "",
        f"- provider: `{meta['provider']}` · model: `{meta['model']}`",
        f"- {meta['documents']} documents × {meta['samples']} samples · prompt `{meta['prompt']}`"
        f" · temperature {meta['temperature'] if meta['temperature'] is not None else 'unset'}",
        "",
        "| | single sample | majority vote |",
        "| --- | --- | --- |",
        f"| field accuracy | {_pct(single['accuracy'])} | {_pct(voted['accuracy'])} |",
        f"| hallucination rate | {_pct(single['hallucination_rate'])} | {_pct(voted['hallucination_rate'])} |",
        f"| omission rate | {_pct(single['omission_rate'])} | {_pct(voted['omission_rate'])} |",
        "",
        f"Cost: {meta['samples']}× the tokens of a single call. Whether that is worth the",
        "delta above is a product decision, not a technical one — but the margin signal",
        "below comes free with it either way.",
        "",
        "## Accuracy by vote margin",
        "",
        "How often the majority answer is right, bucketed by how much of the majority there was.",
        "",
        "| margin | fields | accuracy |",
        "| --- | --- | --- |",
    ]
    for _, label in MARGIN_BUCKETS:
        stats = report["by_margin"][label]
        lines.append(f"| {label} | {stats['fields']} | {_pct(stats['accuracy'])} |")

    disc = report["margin_discrimination"]
    lines += [
        "",
        f"Unanimous fields are **{_pct(disc['unanimous_accuracy'])}** accurate; fields with any "
        f"disagreement are **{_pct(disc['contested_accuracy'])}** accurate "
        f"({disc['contested_fields']} fields).",
        "",
        disc["verdict"],
    ]

    lines += [
        "",
        f"## Abstain rule: send fields with margin < {abstain['threshold']} for review",
        "",
        f"- fields routed to a human: **{abstain['fields_sent_for_review']}**",
        f"- of those, actually wrong: **{abstain['of_which_would_have_been_wrong']}** "
        f"(rule precision {_pct(abstain['precision_of_the_abstain_rule'])})",
        f"- accuracy on the fields kept automatically: **{_pct(abstain['accuracy_on_what_is_kept'])}**",
        "",
        "## Documents the vote moved most",
        "",
        "| doc | single | voted | delta | lowest margin |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["biggest_movers"]:
        lines.append(
            f"| `{row['doc_id']}` | {_pct(row['single_accuracy'])} | {_pct(row['voted_accuracy'])} | "
            f"{row['delta'] * 100:+.1f} pts | {row['min_margin']} |"
        )
    return "\n".join(lines) + "\n"
