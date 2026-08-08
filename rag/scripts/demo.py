#!/usr/bin/env python3
"""End-to-end demo.

    python scripts/demo.py                      # offline: no API key, no network
    python scripts/demo.py --live "question"    # real Claude calls

Offline mode uses the hashing embedder, the lexical reranker, and a scripted
model, so the retrieval → rerank → cite → verify path runs and is inspectable
without credentials. Answer *quality* in offline mode means nothing; answer
*shape* is real.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ragx.config import Config  # noqa: E402
from ragx.factory import build  # noqa: E402
from ragx.obs import metrics  # noqa: E402
from ragx.obs.trace import configure_logging  # noqa: E402
from ragx.types import Principal, Visibility  # noqa: E402

QUESTIONS = [
    "How long do I have to submit an expense claim?",
    "What does ERR_ACCT_4032 mean?",
    "How many days a week am I expected in the office?",
    "What is the parental leave allowance?",
    "What is the salary band for a Staff Engineer?",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use the real Claude API")
    parser.add_argument("--quiet", action="store_true", help="suppress JSON stage logs")
    parser.add_argument("questions", nargs="*", default=None)
    args = parser.parse_args()

    if not args.quiet:
        configure_logging()

    config = Config()
    system = build(config, offline=not args.live)
    system.ingest_directory(ROOT / "corpus", tenant_id="acme")
    print(f"indexed {len(system.vector_store)} chunks from {ROOT / 'corpus'}\n")

    employee = Principal(tenant_id="acme", subject_id="u_1", groups=frozenset({"engineering"}))
    finance = Principal(
        tenant_id="acme",
        subject_id="u_2",
        groups=frozenset({"finance"}),
        max_visibility=Visibility.CONFIDENTIAL,
    )

    for question in args.questions or QUESTIONS:
        for label, principal in (("engineer", employee), ("finance", finance)):
            if "salary band" not in question and label == "finance":
                continue  # only show the permission contrast where it matters
            result = system.pipeline.ask(question, principal)
            answer = result.answer
            print("=" * 78)
            print(f"Q ({label}): {question}")
            print(f"A: {answer.text}")
            print(
                f"   abstained={answer.abstained} grounding={answer.grounding_score:.2f} "
                f"contexts={len(answer.contexts)} total_ms={result.debug['total_ms']}"
            )
            for citation in sorted(answer.citations, key=lambda c: c.marker):
                print(f"   [{citation.marker}] {citation.display_path} — {citation.uri}")
            print()

    print("-" * 78)
    print(metrics.render_prometheus())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
