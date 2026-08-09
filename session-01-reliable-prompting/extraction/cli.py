"""Command line: build the corpus, run extractions, sweep temperature.

    python -m extraction.cli build-corpus
    python -m extraction.cli extract --provider mock --output-mode freeform
    python -m extraction.cli extract --provider anthropic --prompt careful
    python -m extraction.cli compare --provider mock
    python -m extraction.cli sweep --provider mock --repetitions 5
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from . import report as report_mod
from . import scoring
from .client import (
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE_MODEL,
    AnthropicProvider,
    MockProvider,
    has_credentials,
)
from .corpus import Document, generate_corpus, load_corpus, write_corpus
from .pipeline import ExtractConfig, ExtractionResult, extract

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus"
RUNS_DIR = ROOT / "runs"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _require_credentials(args: argparse.Namespace) -> None:
    """Fail before any work is scheduled rather than inside a worker thread."""
    if args.provider == "anthropic" and not has_credentials():
        print(
            "No Anthropic credentials found (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / "
            "an `ant auth login` profile). Re-run with --provider mock to exercise the "
            "harness offline.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _provider(args: argparse.Namespace, *, rep: int = 0, model: Optional[str] = None):
    if args.provider == "mock":
        return MockProvider(seed=args.seed, rep=rep)
    if not has_credentials():
        print(
            "No Anthropic credentials found (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / "
            "an `ant auth login` profile). Re-run with --provider mock to exercise the "
            "harness offline.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return AnthropicProvider(
        model or args.model, effort=args.effort, thinking=args.thinking, max_tokens=args.max_tokens
    )


def _load(args: argparse.Namespace) -> list[Document]:
    corpus_dir = Path(args.corpus)
    if not (corpus_dir / "manifest.json").exists():
        print(f"No corpus at {corpus_dir}. Run: python -m extraction.cli build-corpus", file=sys.stderr)
        raise SystemExit(2)
    docs = load_corpus(corpus_dir)
    if args.limit:
        docs = docs[: args.limit]
    if args.only:
        docs = [d for d in docs if args.only in d.doc_id or args.only in d.tags]
    return docs


def _run_batch(
    docs: list[Document],
    provider_factory,
    config: ExtractConfig,
    *,
    concurrency: int,
    progress: bool = True,
) -> list[ExtractionResult]:
    def one(doc: Document) -> ExtractionResult:
        return extract(
            doc_id=doc.doc_id,
            kind=doc.kind,
            document_text=doc.text,
            provider=provider_factory(),
            config=config,
            injection_canary=doc.injection_canary,
        )

    results: list[ExtractionResult] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for i, result in enumerate(pool.map(one, docs), start=1):
            results.append(result)
            if progress:
                print(f"  [{i:>3}/{len(docs)}] {result.doc_id:<28} {result.status}"
                      f"{'  (+' + str(result.repairs_used) + ' repair)' if result.repairs_used else ''}",
                      file=sys.stderr)
    return results


def _score_all(docs: list[Document], results: list[ExtractionResult]) -> tuple[list[scoring.DocScore], dict[str, Any]]:
    by_id = {d.doc_id: d for d in docs}
    scores = [
        scoring.score_document(
            doc_id=r.doc_id,
            kind=r.kind,
            tags=by_id[r.doc_id].tags,
            truth=by_id[r.doc_id].truth,
            payload=r.payload,
            status=r.status,
            repairs_used=r.repairs_used,
            followed_injection=r.followed_injection,
            refusal_ok=by_id[r.doc_id].refusal_ok,
        )
        for r in results
    ]
    return scores, scoring.aggregate(scores, results)


def _write_run(label: str, meta: dict[str, Any], results: list[ExtractionResult],
               scores: list[scoring.DocScore], summary: dict[str, Any]) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / label
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out / "scores.json").write_text(
        json.dumps([asdict(s) | {"outcomes": dict(s.outcomes)} for s in scores], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out / "summary.json").write_text(
        json.dumps({"meta": meta, "summary": summary}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out / "report.md").write_text(report_mod.run_report(summary, meta), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_build_corpus(args: argparse.Namespace) -> int:
    docs = generate_corpus(seed=args.seed, size=args.size)
    manifest = write_corpus(docs, Path(args.corpus))
    kinds = {}
    media = {}
    for doc in docs:
        kinds[doc.kind] = kinds.get(doc.kind, 0) + 1
        media[doc.medium] = media.get(doc.medium, 0) + 1
    hard = sum(1 for d in docs if any(t.startswith("hard:") for t in d.tags))
    print(f"wrote {len(docs)} documents -> {manifest.parent}")
    print(f"  kinds: {kinds}")
    print(f"  media: {media}")
    print(f"  documents with a planted failure mode: {hard}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    _require_credentials(args)
    docs = _load(args)
    config = ExtractConfig(
        prompt=args.prompt,
        output_mode=args.output_mode,
        max_attempts=args.max_attempts,
        repair_ungrounded=not args.no_grounding_repair,
        temperature=args.temperature,
    )
    label = args.label or f"{args.provider}-{args.prompt}-{args.output_mode}"
    print(f"extracting {len(docs)} documents [{label}]", file=sys.stderr)
    results = _run_batch(docs, lambda: _provider(args), config, concurrency=args.concurrency)
    scores, summary = _score_all(docs, results)
    meta = {
        "label": label,
        "provider": args.provider,
        "model": results[0].model if results else args.model,
        "prompt": args.prompt,
        "output_mode": args.output_mode,
        "max_attempts": args.max_attempts,
        "repair_ungrounded": not args.no_grounding_repair,
        "temperature": args.temperature,
    }
    out = _write_run(label, meta, results, scores, summary)
    print(report_mod.run_report(summary, meta))
    print(f"artefacts: {out}", file=sys.stderr)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Run the four prompt × output-mode arms and print the contrast table."""
    _require_credentials(args)
    docs = _load(args)
    arms = [
        ("naive-freeform", "naive", "freeform", 1),
        ("careful-freeform", "careful", "freeform", 1),
        ("careful-freeform-repair", "careful", "freeform", args.max_attempts),
        ("careful-strict-repair", "careful", "strict", args.max_attempts),
    ]
    rows = []
    for label, prompt, mode, attempts in arms:
        config = ExtractConfig(
            prompt=prompt, output_mode=mode, max_attempts=attempts,
            repair_ungrounded=not args.no_grounding_repair, temperature=args.temperature,
        )
        print(f"\n=== {label} ===", file=sys.stderr)
        results = _run_batch(docs, lambda: _provider(args), config, concurrency=args.concurrency)
        scores, summary = _score_all(docs, results)
        meta = {
            "label": label, "provider": args.provider,
            "model": results[0].model if results else args.model,
            "prompt": prompt, "output_mode": mode, "max_attempts": attempts,
            "repair_ungrounded": not args.no_grounding_repair, "temperature": args.temperature,
        }
        _write_run(label, meta, results, scores, summary)
        rows.append((label, summary))

    header = (
        "| arm | valid 1st try | accuracy | hallucination rate | injection followed | refusals |\n"
        "| --- | --- | --- | --- | --- | --- |"
    )
    lines = [header]
    for label, summary in rows:
        inj = summary["injection"]
        lines.append(
            f"| `{label}` | {report_mod._pct(summary['valid_first_try'])} | "
            f"{report_mod._pct(summary['accuracy'])} | "
            f"{report_mod._pct(summary['hallucination_rate'])} | "
            f"{inj['followed']}/{inj['documents']} | {summary['refusals']['total']} |"
        )
    table = "\n".join(lines)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / "comparison.md").write_text("# Prompt / output-mode comparison\n\n" + table + "\n",
                                            encoding="utf-8")
    print("\n" + table)
    print(f"\nartefacts: {RUNS_DIR / 'comparison.md'}", file=sys.stderr)
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    from experiments.temperature import run_sweep  # noqa: PLC0415 - optional import path

    return run_sweep(args)


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="extraction", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--corpus", default=str(CORPUS_DIR), help="corpus directory")
        p.add_argument("--provider", choices=["mock", "anthropic"], default="mock")
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="medium")
        p.add_argument("--thinking", choices=["adaptive", "disabled"], default="adaptive")
        p.add_argument("--max-tokens", type=int, default=8000)
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--limit", type=int, default=0, help="only the first N documents")
        p.add_argument("--only", default="", help="filter by doc_id substring or tag")
        p.add_argument("--concurrency", type=int, default=4)

    build = sub.add_parser("build-corpus", help="generate the 50-document corpus")
    build.add_argument("--corpus", default=str(CORPUS_DIR))
    build.add_argument("--seed", type=int, default=7)
    build.add_argument("--size", type=int, default=50)
    build.set_defaults(func=cmd_build_corpus)

    run = sub.add_parser("extract", help="run one extraction configuration over the corpus")
    common(run)
    run.add_argument("--prompt", choices=["naive", "careful"], default="careful")
    run.add_argument("--output-mode", choices=["strict", "freeform"], default="strict")
    run.add_argument("--max-attempts", type=int, default=3)
    run.add_argument("--no-grounding-repair", action="store_true",
                     help="detect ungrounded values but do not spend a repair turn on them")
    run.add_argument("--temperature", type=float, default=None)
    run.add_argument("--label", default="")
    run.set_defaults(func=cmd_extract)

    compare = sub.add_parser("compare", help="naive vs careful, freeform vs structured outputs")
    common(compare)
    compare.add_argument("--max-attempts", type=int, default=3)
    compare.add_argument("--no-grounding-repair", action="store_true")
    compare.add_argument("--temperature", type=float, default=None)
    compare.set_defaults(func=cmd_compare)

    sweep = sub.add_parser("sweep", help="temperature 0 vs 0.7")
    common(sweep)
    sweep.set_defaults(model=DEFAULT_TEMPERATURE_MODEL)
    sweep.add_argument("--prompt", choices=["naive", "careful"], default="careful")
    sweep.add_argument("--output-mode", choices=["strict", "freeform"], default="strict")
    sweep.add_argument("--max-attempts", type=int, default=3)
    sweep.add_argument("--no-grounding-repair", action="store_true")
    sweep.add_argument("--repetitions", type=int, default=5)
    sweep.add_argument("--temperatures", default="0,0.7",
                       help="comma-separated list of temperatures to compare")
    sweep.set_defaults(func=cmd_sweep)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
