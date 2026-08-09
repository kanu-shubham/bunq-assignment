"""temperature = 0 vs 0.7.

Each document is extracted `--repetitions` times at each temperature and the
results are compared *to each other*, not only to the ground truth.  Two
measurements come out:

*   **field agreement** — the share of fields on which every repetition of the
    same document produced the same value.  This is the number that makes
    stochasticity concrete: at t=0 it should sit near 1.0, and whatever it is at
    t=0.7 is the fraction of your pipeline that is a coin flip.
*   **identical outputs** — the share of documents where every repetition
    serialised to the same object.  Harsher, and closer to what a downstream
    consumer actually experiences.

Accuracy and hallucination rate are reported alongside, because the interesting
result is usually that t=0.7 costs you agreement *without* buying accuracy.

Two caveats to keep in view when reading the output:

1.  `temperature` was removed from Claude Opus 5, Opus 4.8/4.7 and Fable 5 —
    sending it is a 400 — and Sonnet 5 rejects non-default values.  The sweep
    therefore defaults to `claude-sonnet-4-6`, which still exposes the knob.
    On a model without it, run the sweep with a single temperature (`0`,
    unset) to measure the *residual* nondeterminism you get for free.
2.  temperature=0 is greedy decoding, not a determinism guarantee.  Batching,
    routing and floating-point non-associativity all leak through.  A t=0 field
    agreement below 1.0 is the expected result, not a bug in the harness.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from extraction import report as report_mod
from extraction import scoring
from extraction.cli import _load, _provider, _require_credentials, _run_batch, _score_all
from extraction.client import NO_SAMPLING_PARAMS, REJECTS_NONDEFAULT_SAMPLING
from extraction.pipeline import ExtractConfig

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


def run_sweep(args: argparse.Namespace) -> int:
    _require_credentials(args)
    temperatures = [float(t) for t in str(args.temperatures).split(",") if t.strip() != ""]

    if args.provider == "anthropic":
        blocked = [t for t in temperatures if _blocked(args.model, t)]
        if blocked:
            print(
                f"model {args.model!r} rejects temperature={blocked[0]}: sampling parameters were "
                f"removed on {sorted(NO_SAMPLING_PARAMS)} and Sonnet 5 only accepts the default.\n"
                f"Re-run with --model claude-sonnet-4-6 (or another temperature-capable model), "
                f"or with --temperatures 0 to measure residual nondeterminism instead.",
                file=sys.stderr,
            )
            return 2

    docs = _load(args)
    arms: list[dict[str, Any]] = []

    for temperature in temperatures:
        print(f"\n=== temperature {temperature} · {args.repetitions} repetitions ===", file=sys.stderr)
        config = ExtractConfig(
            prompt=args.prompt,
            output_mode=args.output_mode,
            max_attempts=args.max_attempts,
            repair_ungrounded=not args.no_grounding_repair,
            temperature=temperature,
        )
        per_rep = []
        for rep in range(args.repetitions):
            print(f"  repetition {rep + 1}/{args.repetitions}", file=sys.stderr)
            results = _run_batch(
                docs, lambda rep=rep: _provider(args, rep=rep), config,
                concurrency=args.concurrency, progress=False,
            )
            per_rep.append({r.doc_id: r for r in results})

        arms.append(_summarise_arm(docs, per_rep, temperature))

    meta = {
        "provider": args.provider,
        "model": args.model if args.provider == "anthropic" else "mock-1",
        "documents": len(docs),
        "repetitions": args.repetitions,
        "prompt": args.prompt,
        "output_mode": args.output_mode,
        "temperatures": temperatures,
    }
    sweep = {"meta": meta, "arms": arms}

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / "temperature-sweep"
    out.mkdir(parents=True, exist_ok=True)
    (out / "sweep.json").write_text(json.dumps(sweep, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(report_mod.sweep_report(sweep), encoding="utf-8")
    print(report_mod.sweep_report(sweep))
    print(f"artefacts: {out}", file=sys.stderr)
    return 0


def _blocked(model: str, temperature: float) -> bool:
    if model in NO_SAMPLING_PARAMS:
        return True
    return model in REJECTS_NONDEFAULT_SAMPLING and temperature != 1.0


def _summarise_arm(docs, per_rep: list[dict[str, Any]], temperature: float) -> dict[str, Any]:
    agreements: list[float] = []
    identical = 0
    unstable_docs: list[dict[str, Any]] = []
    field_churn: Counter = Counter()

    for doc in docs:
        payloads = [rep[doc.doc_id].payload for rep in per_rep if doc.doc_id in rep]
        stats = scoring.agreement(payloads)
        agreements.append(stats["field_agreement"])
        if stats["distinct_outputs"] == 1:
            identical += 1
        else:
            unstable_docs.append(
                {
                    "doc_id": doc.doc_id,
                    "distinct_outputs": stats["distinct_outputs"],
                    "unstable_fields": stats["unstable_fields"],
                }
            )
        field_churn.update(stats["unstable_fields"])

    # Accuracy is measured on the first repetition so the number is comparable
    # with a single-pass run.
    first = [per_rep[0][doc.doc_id] for doc in docs if doc.doc_id in per_rep[0]]
    _, summary = _score_all(docs, first)

    return {
        "temperature": temperature,
        "mean_field_agreement": round(sum(agreements) / len(agreements), 4) if agreements else None,
        "identical_output_rate": round(identical / len(docs), 4) if docs else None,
        "accuracy": summary["accuracy"],
        "hallucination_rate": summary["hallucination_rate"],
        "valid_first_try": summary["valid_first_try"],
        "most_unstable_fields": field_churn.most_common(),
        "unstable_documents": sorted(unstable_docs, key=lambda d: -d["distinct_outputs"]),
    }


def main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover
    from extraction.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["sweep", *(argv or [])])
    return run_sweep(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
