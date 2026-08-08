#!/usr/bin/env python3
"""Run the golden-set evaluation and gate on thresholds.

    python scripts/run_eval.py                    # offline, no API key
    python scripts/run_eval.py --live --judge     # real models + LLM judge
    python scripts/run_eval.py --no-rerank        # ablation

Exit code is non-zero when a threshold fails, so this drops straight into CI.
Ablations (`--no-rerank`, `--fusion weighted`, `--final-k`) exist because the
only way to justify a stage is to show the numbers with and without it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ragx.config import JUDGE_MODEL, Config  # noqa: E402
from ragx.eval.dataset import load_cases  # noqa: E402
from ragx.eval.judge import Judge  # noqa: E402
from ragx.eval.runner import (  # noqa: E402
    Thresholds,
    gated_failures,
    run_eval,
    write_report,
)
from ragx.factory import build  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--judge", action="store_true", help="run the LLM judge (implies --live)")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--fusion", choices=["rrf", "weighted"], default="rrf")
    parser.add_argument("--final-k", type=int, default=None)
    parser.add_argument("--cases", type=Path, default=ROOT / "evalset" / "golden.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "eval-out")
    parser.add_argument("--strict", action="store_true", help="fail the process on any threshold miss")
    parser.add_argument(
        "--gate",
        choices=["all", "retrieval"],
        default="all",
        help=(
            "which thresholds --strict enforces. 'retrieval' gates recall/nDCG/MRR and "
            "permission leaks only — the metrics that are meaningful without model calls, "
            "so an offline CI job can fail on a real regression without spending tokens."
        ),
    )
    args = parser.parse_args()

    live = args.live or args.judge
    config = Config()
    config = replace(
        config,
        retrieval=replace(
            config.retrieval,
            fusion=args.fusion,
            final_k=args.final_k or config.retrieval.final_k,
        ),
        rerank=replace(config.rerank, enabled=not args.no_rerank),
    )

    system = build(config, offline=not live)
    system.ingest_directory(ROOT / "corpus", tenant_id="acme")

    cases = load_cases(args.cases)
    judge = Judge(system.llm, JUDGE_MODEL) if args.judge else None

    report = run_eval(system, cases, judge=judge, thresholds=Thresholds())
    json_path, md_path = write_report(report, args.out)

    print(report.to_markdown())
    print(f"\nwrote {json_path} and {md_path}")

    failures = report.failures()
    gated = gated_failures(failures, args.gate)

    if failures:
        print("\nFAILED thresholds:")
        for failure in failures:
            marker = "gating" if failure in gated else "reported"
            print(f"  - [{marker}] {failure}")
    else:
        print("\nall thresholds passed")

    return 1 if (gated and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
