#!/usr/bin/env python3
"""Ask the corpus a question, end to end, with citations and abstention.

    python scripts/ask.py "how do I roll back a deploy"
    python scripts/ask.py "what is the capital of France"     # abstains
    python scripts/ask.py "retry backoff" --doc-type postmortem
    python scripts/ask.py "how do I roll back a deploy" --claude
    python scripts/ask.py --demo                              # a scripted tour

By default this runs entirely offline: the extractive answerer selects and cites
sentences from the retrieved chunks, so it can be wrong about *which* sentence
answers the question but cannot invent one. ``--claude`` swaps in the Messages
API with structured outputs and the same mechanical citation verification
applied to whatever it returns.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import (  # noqa: E402
    ExtractiveAnswerer,
    LexicalCrossEncoder,
    MetadataFilter,
    PipelineConfig,
    RagPipeline,
    load_corpus,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]

DEMO_QUESTIONS = [
    ("how do I roll back a deploy", None),
    ("what is the webhook retry schedule", None),
    ("what happens if the fraud scoring service is too slow", None),
    ("what is the capital of France", None),
    ("what is our policy on unlimited vacation", None),
    ("duplicate submission", "postmortem"),
]


def ask(pipeline, answerer, question: str, doc_type: str | None, k: int, verbose: bool) -> None:
    mfilter = MetadataFilter(equals={"doc_type": doc_type}) if doc_type else None
    result = pipeline.retrieve(question, k, metadata_filter=mfilter)
    answer = answerer.answer(question, result.chunks)

    print(f"\n{'=' * 74}")
    print(f"Q: {question}" + (f"   [doc_type={doc_type}]" if doc_type else ""))
    print("=" * 74)

    if answer.abstained:
        print(f"\n  ABSTAINED ({answer.abstain_reason.value})")
        print(f"  {answer.text}")
        if answer.trace:
            print(f"  trace: {answer.trace}")
        if verbose and result.chunks:
            print("\n  closest documents retrieved:")
            for sc in result.chunks[:3]:
                print(f"    {sc.score:.3f}  {sc.doc_id}")
        return

    print(f"\n{answer.text}\n")
    print("  sources:")
    for citation in answer.citations:
        mark = "verified" if citation.verified else f"UNVERIFIED ({citation.note})"
        print(f"    [{citation.index}] {citation.doc_id:<44} {mark}")
    print(f"\n  fully supported : {answer.fully_supported}")
    print(f"  citation coverage: {answer.trace.get('coverage')}")

    if verbose:
        print(f"\n  retrieval trace: {result.trace}")
        print(f"  variants issued: {list(result.rewritten_queries)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*", help="the question to ask")
    parser.add_argument("--doc-type", default=None)
    parser.add_argument("-k", type=int, default=5, help="chunks retrieved into context")
    parser.add_argument("--claude", action="store_true", help="use the Messages API instead of extraction")
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--demo", action="store_true", help="run a scripted set of questions")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not args.question and not args.demo:
        parser.error("give a question, or pass --demo")

    docs = load_corpus(ROOT / "data" / "corpus")
    pipeline = RagPipeline.build(docs, PipelineConfig(top_k=args.k, use_cache=True))

    if args.claude:
        from ragkit import ClaudeAnswerer

        answerer = ClaudeAnswerer(model=args.model)
        print(f"answering with {args.model} (structured outputs + citation verification)")
    else:
        answerer = ExtractiveAnswerer(LexicalCrossEncoder.from_embedder(pipeline.embedder))

    if args.demo:
        for question, doc_type in DEMO_QUESTIONS:
            ask(pipeline, answerer, question, doc_type, args.k, args.verbose)
        print(
            f"\n{'=' * 74}\n"
            "Read the last two carefully — one works and one does not, and the one\n"
            "that does not is the more instructive.\n\n"
            "'unlimited vacation' abstains. Good: retrieval returns the leave policy\n"
            "(which says 27 days), and the relevance gate rejects it before anything\n"
            "gets a chance to agree with the premise.\n\n"
            "'capital of France' does NOT abstain. It reaches 'capital statements' in\n"
            "the tax-reporting README and scores just above the gate. Every sentence\n"
            "it returns is real and verifies against its source — and the answer is\n"
            "still useless. That is precisely the limit of a relevance threshold:\n"
            "it scores whether a passage is about a topic, not whether it answers the\n"
            "question. Run scripts/calibrate_abstention.py to see the data behind\n"
            "that, and note that pushing the gate high enough to catch this one\n"
            "would reject roughly three quarters of the questions the corpus can\n"
            "genuinely answer.\n\n"
            "The fix is not a better threshold. It is an entailment check — asking a\n"
            "model whether the context answers the question and taking 'no' for an\n"
            "answer, which is what --claude does via the `answerable` field.\n"
        )
    else:
        ask(pipeline, answerer, " ".join(args.question), args.doc_type, args.k, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
