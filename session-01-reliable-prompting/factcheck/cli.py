"""Fact-checker CLI.

    python -m factcheck.cli check --text "Northwind grew 12% to EUR 4.1bn in 2024."
    python -m factcheck.cli check --document DOC-compound
    python -m factcheck.cli eval                       # both eval levels
    python -m factcheck.cli eval --ablate              # what each stage is worth
    python -m factcheck.cli sources                    # the evidence corpus
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from extraction.cli import _require_credentials
from extraction.client import DEFAULT_MODEL, AnthropicProvider, MockProvider

from . import evalset, pipeline, report
from .evidence import CORPUS, build_retriever
from .schemas import FactCheckReport


def _provider_factory(args: argparse.Namespace):
    def factory():
        if args.provider == "mock":
            return MockProvider(seed=args.seed)
        return AnthropicProvider(args.model, effort=args.effort, thinking=args.thinking)

    return factory


def _config(args: argparse.Namespace) -> pipeline.FactCheckConfig:
    return pipeline.FactCheckConfig(
        queries_per_claim=args.queries,
        passages_per_query=args.passages,
        max_passages_per_claim=args.max_passages,
        max_attempts=args.max_attempts,
        temperature=args.temperature,
        concurrency=args.concurrency,
        skip_planning=args.skip_planning,
    )


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #


def cmd_check(args: argparse.Namespace) -> int:
    _require_credentials(args)
    if args.document:
        case = next((d for d in evalset.DOCUMENTS if d.doc_id == args.document), None)
        if case is None:
            print(f"unknown document {args.document!r}; try: "
                  f"{', '.join(d.doc_id for d in evalset.DOCUMENTS)}", file=sys.stderr)
            return 2
        text, doc_id = case.text, case.doc_id
    elif args.text:
        text, doc_id = args.text, "input"
    elif not sys.stdin.isatty():
        text, doc_id = sys.stdin.read(), "stdin"
    else:
        print("nothing to check: pass --text, --document, or pipe text on stdin", file=sys.stderr)
        return 2

    result = pipeline.check_document(
        text,
        provider_factory=_provider_factory(args),
        retriever=build_retriever(args.retriever),
        cfg=_config(args),
        document_id=doc_id,
    )
    rendered = report.render_check(result, text)
    path = evalset.write(result.model_dump(), f"check-{doc_id}.json")
    (path.parent / f"check-{doc_id}.md").write_text(rendered, encoding="utf-8")
    print(rendered)
    print(f"artefacts: {path.parent}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# eval
# --------------------------------------------------------------------------- #


def _run_claim_cases(args: argparse.Namespace, cfg: pipeline.FactCheckConfig) -> list[evalset.ClaimResult]:
    retriever = build_retriever(args.retriever)
    factory = _provider_factory(args)

    def one(case: evalset.ClaimCase) -> evalset.ClaimResult:
        verdict = pipeline.check_claim(
            case.to_claim(), provider=factory(), retriever=retriever, cfg=cfg
        )
        correct = verdict.verdict == case.gold
        dangerous = case.slice in evalset.DANGEROUS_SLICES and verdict.verdict == "supported"
        return evalset.ClaimResult(
            claim=case.claim,
            slice=case.slice,
            gold=case.gold,
            got=verdict.verdict,
            confidence=verdict.confidence,
            margin=verdict.margin,
            correct=correct,
            dangerous=dangerous,
            reason=verdict.reason,
            evidence_sources=sorted({e.source_id for e in verdict.evidence}),
            ungrounded_quotes=verdict.discarded_ungrounded_quotes,
        )

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        return list(pool.map(one, evalset.CLAIM_CASES))


def _run_document_cases(args: argparse.Namespace, cfg: pipeline.FactCheckConfig) -> list[dict[str, Any]]:
    retriever = build_retriever(args.retriever)
    factory = _provider_factory(args)
    rows = []
    for case in evalset.DOCUMENTS:
        result: FactCheckReport = pipeline.check_document(
            case.text, provider_factory=factory, retriever=retriever, cfg=cfg,
            document_id=case.doc_id,
        )
        checkable = [v for v in result.verdicts if v.verdict != "not_checkable"]
        rows.append(
            {
                "doc_id": case.doc_id,
                "note": case.note,
                "gold_claims": case.gold_claims,
                "found_claims": len(result.verdicts),
                "gold_checkable": case.gold_checkable,
                "found_checkable": len(checkable),
                "discarded_spans": sum(1 for p in result.stage_failures if "span" in p),
                "stage_failures": result.stage_failures,
                "verdicts": [
                    {"claim": v.claim.text, "verdict": v.verdict, "confidence": v.confidence}
                    for v in result.verdicts
                ],
            }
        )
    return rows


def cmd_eval(args: argparse.Namespace) -> int:
    _require_credentials(args)
    cfg = _config(args)

    print(f"claim-level eval: {len(evalset.CLAIM_CASES)} cases", file=sys.stderr)
    claim_results = _run_claim_cases(args, cfg)
    claim_summary = evalset.score_claim_results(claim_results)

    print(f"document-level eval: {len(evalset.DOCUMENTS)} documents", file=sys.stderr)
    document_rows = _run_document_cases(args, cfg)

    ablations: list[dict[str, Any]] = []
    if args.ablate:
        # Each ablation answers "what is this stage worth?" — the only honest way
        # to justify a stage's cost.
        variants = [
            ("full pipeline", cfg),
            ("no query planning (search the claim text)",
             pipeline.FactCheckConfig(**{**vars(cfg), "skip_planning": True})),
            ("one passage per claim",
             pipeline.FactCheckConfig(**{**vars(cfg), "max_passages_per_claim": 1})),
        ]
        for label, variant in variants:
            print(f"  ablation: {label}", file=sys.stderr)
            results = _run_claim_cases(args, variant)
            summary = evalset.score_claim_results(results)
            ablations.append(
                {
                    "variant": label,
                    "accuracy": summary["accuracy"],
                    "dangerous_errors": summary["dangerous_errors"]["count"],
                    "abstention_precision": summary["abstention"]["precision"],
                }
            )

    payload = {
        "meta": {
            "provider": args.provider,
            "model": args.model if args.provider == "anthropic" else "mock-1",
            "retriever": args.retriever,
            "evidence_documents": len(CORPUS),
            "queries_per_claim": args.queries,
            "max_passages_per_claim": args.max_passages,
        },
        "claim_level": claim_summary,
        "document_level": document_rows,
        "ablations": ablations,
    }
    path = evalset.write(payload, "eval.json")
    rendered = report.render_eval(payload)
    (path.parent / "report.md").write_text(rendered, encoding="utf-8")
    print(rendered)
    print(f"artefacts: {path.parent}", file=sys.stderr)
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    print(json.dumps(
        [
            {"source_id": d.source_id, "title": d.title, "publisher": d.publisher,
             "published": d.published, "primary": d.is_primary, "reliability": d.reliability}
            for d in CORPUS
        ],
        indent=2,
    ))
    return 0


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factcheck", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--provider", choices=["mock", "anthropic"], default="mock")
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                       default="medium")
        p.add_argument("--thinking", choices=["adaptive", "disabled"], default="adaptive")
        p.add_argument("--retriever", choices=["local", "web"], default="local")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--queries", type=int, default=3)
        p.add_argument("--passages", type=int, default=3)
        p.add_argument("--max-passages", type=int, default=5)
        p.add_argument("--max-attempts", type=int, default=2)
        p.add_argument("--temperature", type=float, default=None)
        p.add_argument("--concurrency", type=int, default=4)
        p.add_argument("--skip-planning", action="store_true",
                       help="baseline: search the claim text instead of planned queries")

    check = sub.add_parser("check", help="fact-check a document")
    common(check)
    check.add_argument("--text", default="")
    check.add_argument("--document", default="", help="an eval document id, e.g. DOC-compound")
    check.set_defaults(func=cmd_check)

    ev = sub.add_parser("eval", help="score the pipeline against the labelled set")
    common(ev)
    ev.add_argument("--ablate", action="store_true", help="also measure what each stage is worth")
    ev.set_defaults(func=cmd_eval)

    src = sub.add_parser("sources", help="list the evidence corpus")
    common(src)
    src.set_defaults(func=cmd_sources)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
