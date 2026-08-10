"""Markdown rendering for the fact-checker."""

from __future__ import annotations

from typing import Any, Optional

from .schemas import FactCheckReport

_ICON = {
    "supported": "✅ supported",
    "refuted": "❌ refuted",
    "unsupported": "⚠️ unsupported",
    "not_checkable": "— not checkable",
}


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_check(result: FactCheckReport, source_text: str) -> str:
    lines = [
        f"# Fact check — {result.document_id}",
        "",
        "> " + source_text.strip().replace("\n", "\n> "),
        "",
    ]
    checkable = [v for v in result.verdicts if v.verdict != "not_checkable"]
    lines += [
        f"{len(result.verdicts)} claim(s) found, {len(checkable)} checkable.",
        "",
    ]
    for verdict in result.verdicts:
        lines += [
            f"### {_ICON[verdict.verdict]} — {verdict.claim.text}",
            "",
            f"- confidence {verdict.confidence:.2f} · margin {verdict.margin:.2f} · "
            f"{verdict.passages_examined} passage(s) examined",
            f"- {verdict.reason}",
        ]
        if verdict.evidence:
            lines.append("")
            for ref in verdict.evidence:
                mark = "" if ref.quote_grounded else " *(quote not in the passage — discarded)*"
                primary = " · primary" if ref.is_primary else ""
                lines.append(
                    f"  - **{ref.relation}** — {ref.publisher}, {ref.published or 'undated'}"
                    f"{primary} (`{ref.source_id}`){mark}"
                )
                if ref.quote:
                    lines.append(f"    > {ref.quote}")
        lines.append("")

    if result.skipped:
        lines += ["## Not checked", ""] + [f"- {item}" for item in result.skipped] + [""]
    if result.stage_failures:
        lines += ["## Stage failures", ""] + [f"- {item}" for item in result.stage_failures] + [""]
    if result.injection_attempts_seen:
        lines += [
            f"## ⚠️ {result.injection_attempts_seen} retrieved passage(s) addressed the checker",
            "",
            "Those passages were weighted to zero at aggregation. The count is reported rather "
            "than swallowed so it can be watched over time.",
            "",
        ]
    return "\n".join(lines)


def render_eval(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    claim = payload["claim_level"]
    dangerous = claim["dangerous_errors"]
    abstention = claim["abstention"]

    lines = [
        "# Fact-checking system — evaluation",
        "",
        f"- provider: `{meta['provider']}` · model: `{meta['model']}` · retriever: "
        f"`{meta['retriever']}` over {meta['evidence_documents']} evidence documents",
        f"- {meta['queries_per_claim']} queries per claim, up to "
        f"{meta['max_passages_per_claim']} passages verified per claim",
        "",
        "## Claim level",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| claims | {claim['claims']} |",
        f"| verdict accuracy | {_pct(claim['accuracy'])} |",
        f"| **dangerous errors** (a refuted claim reported as supported) | "
        f"**{dangerous['count']}** ({_pct(dangerous['rate'])}) |",
        f"| abstained (`unsupported`) | {abstention['abstained']} |",
        f"| of which correctly | {abstention['correctly']} (precision {_pct(abstention['precision'])}) |",
        f"| verdicts discarded for an ungrounded quote | {claim['ungrounded_quotes_discarded']} |",
        "",
        "Accuracy alone is the wrong headline. A system that answers `unsupported` to",
        "everything scores well on never being wrong and is useless; one that answers",
        "`supported` to everything has zero abstentions and does real damage. Read the",
        "dangerous-error count and the abstention precision next to it.",
        "",
        "## By slice",
        "",
        "| slice | n | accuracy | dangerous |",
        "| --- | --- | --- | --- |",
    ]
    for name, stats in claim["by_slice"].items():
        lines.append(
            f"| `{name}` | {stats['n']} | {_pct(stats['accuracy'])} | {stats['dangerous']} |"
        )

    lines += ["", "## Confusion (gold → produced)", "", "| gold | produced |", "| --- | --- |"]
    for gold, got in sorted(claim["confusion"].items()):
        rendered = ", ".join(f"{k} ×{v}" for k, v in sorted(got.items(), key=lambda kv: -kv[1]))
        lines.append(f"| `{gold}` | {rendered} |")

    if dangerous["claims"]:
        lines += ["", "### Claims reported as supported that are actually refuted", ""]
        lines += [f"- {c}" for c in dangerous["claims"]]

    lines += [
        "",
        "## Document level (stage 1 — decomposition)",
        "",
        "Decomposition decides what everything downstream is right or wrong about, so it is",
        "measured separately. `found` below counts claims that survived the span check.",
        "",
        "| document | gold claims | found | gold checkable | found checkable | spans discarded |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in payload["document_level"]:
        lines.append(
            f"| `{row['doc_id']}` | {row['gold_claims']} | {row['found_claims']} | "
            f"{row['gold_checkable']} | {row['found_checkable']} | {row['discarded_spans']} |"
        )
    lines += [""] + [f"- `{row['doc_id']}` — {row['note']}" for row in payload["document_level"]]

    if payload.get("ablations"):
        lines += [
            "",
            "## Ablations — what is each stage worth?",
            "",
            "| variant | accuracy | dangerous errors | abstention precision |",
            "| --- | --- | --- | --- |",
        ]
        for row in payload["ablations"]:
            lines.append(
                f"| {row['variant']} | {_pct(row['accuracy'])} | {row['dangerous_errors']} | "
                f"{_pct(row['abstention_precision'])} |"
            )
        lines += [
            "",
            "A stage that does not move these numbers is a stage to delete, not to tune.",
        ]
    return "\n".join(lines) + "\n"
