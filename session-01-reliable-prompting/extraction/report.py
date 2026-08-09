"""Markdown rendering for run summaries and the temperature sweep."""

from __future__ import annotations

from typing import Any, Optional


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:g}"


def run_report(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    status = summary["status"]
    outcomes = summary["field_outcomes"]
    lines = [
        f"# Extraction run — {meta.get('label', 'run')}",
        "",
        f"- provider: `{meta.get('provider')}` · model: `{meta.get('model')}`",
        f"- prompt: `{meta.get('prompt')}` · output mode: `{meta.get('output_mode')}`"
        f" · max attempts: {meta.get('max_attempts')}"
        f" · grounding repair: {meta.get('repair_ungrounded')}",
        f"- temperature: {meta.get('temperature') if meta.get('temperature') is not None else 'unset (model default)'}",
        f"- documents: {summary['documents']}",
        "",
        "## Reliability",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| valid on first attempt | {_pct(summary['valid_first_try'])} |",
        f"| valid *and* drift-free on first attempt | {_pct(summary['clean_first_try'])} |",
        f"| rescued by the repair loop | {summary['repaired_to_valid']} documents |",
        f"| mean repair round-trips | {_num(summary['mean_repairs'])} |",
        f"| ended `ok` / `invalid` / `refused` / `error` | "
        f"{status.get('ok', 0)} / {status.get('invalid', 0)} / "
        f"{status.get('refused', 0)} / {status.get('error', 0)} |",
        "",
        "## Field accuracy",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| overall field accuracy | {_pct(summary['accuracy'])} |",
        f"| accuracy on fields that have a value | {_pct(summary['value_accuracy'])} |",
        f"| **hallucination rate** (invented a value where the document has none) | "
        f"{_pct(summary['hallucination_rate'])} |",
        f"| omission rate (returned null where the document has a value) | {_pct(summary['omission_rate'])} |",
        "",
        "| outcome | fields |",
        "| --- | --- |",
    ]
    for key in ("correct", "correct_null", "hallucination", "omission", "wrong"):
        lines.append(f"| {key} | {outcomes.get(key, 0)} |")

    refusals = summary["refusals"]
    injection = summary["injection"]
    lines += [
        "",
        "## Refusals and injection",
        "",
        f"- refusals: **{refusals['total']}** "
        f"({refusals['on_refusal_bait']} of {refusals['refusal_bait_documents']} on documents where a "
        f"refusal is defensible)",
    ]
    if refusals["unjustified"]:
        lines.append(f"- refused on ordinary documents: {', '.join(refusals['unjustified'])}")
    lines.append(
        f"- prompt injection followed: **{injection['followed']} / {injection['documents']}** "
        "documents carrying an embedded override"
    )

    drift = summary["format_drift"]
    lines += ["", "## Format drift (what salvage had to fix)", ""]
    if drift:
        lines += ["| recovery | occurrences |", "| --- | --- |"]
        lines += [f"| {k} | {v} |" for k, v in sorted(drift.items(), key=lambda kv: -kv[1])]
    else:
        lines.append("None — every response was a bare JSON object.")

    lines += ["", "## By failure mode", "", "| tag | docs | accuracy | hallucinations | refused |",
              "| --- | --- | --- | --- | --- |"]
    for tag, stats in summary["by_tag"].items():
        lines.append(
            f"| `{tag}` | {stats['documents']} | {_pct(stats['accuracy'])} | "
            f"{stats['hallucinations']} | {stats['refused']} |"
        )
    return "\n".join(lines) + "\n"


def sweep_report(sweep: dict[str, Any]) -> str:
    meta = sweep["meta"]
    lines = [
        "# temperature 0 vs 0.7",
        "",
        f"- provider: `{meta['provider']}` · model: `{meta['model']}`",
        f"- documents: {meta['documents']} · repetitions per setting: {meta['repetitions']}",
        f"- prompt: `{meta['prompt']}` · output mode: `{meta['output_mode']}`",
        "",
        "`field agreement` is the share of fields on which every repetition of the same document",
        "produced the same value. `identical outputs` is the share of documents where all",
        "repetitions serialised to the same object — the strict version of the same question.",
        "",
        "| temperature | field agreement | identical outputs | accuracy | hallucination rate | valid first try |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for arm in sweep["arms"]:
        lines.append(
            f"| {arm['temperature']} | {_pct(arm['mean_field_agreement'])} | "
            f"{_pct(arm['identical_output_rate'])} | {_pct(arm['accuracy'])} | "
            f"{_pct(arm['hallucination_rate'])} | {_pct(arm['valid_first_try'])} |"
        )

    lines += ["", "## Fields that moved most between repetitions", ""]
    for arm in sweep["arms"]:
        top = arm["most_unstable_fields"][:8]
        rendered = ", ".join(f"`{name}` ×{count}" for name, count in top) or "none"
        lines.append(f"- **t={arm['temperature']}**: {rendered}")

    lines += [
        "",
        "## Documents whose answer changed between repetitions",
        "",
        "A row with no unstable fields changed only in *serialisation* — casing, trailing",
        "punctuation, key order. The values compare equal, but a byte-for-byte consumer",
        "(a hash, a cache key, a diff in review) still sees a different answer every run.",
        "",
        "| temperature | doc | distinct outputs | unstable fields |",
        "| --- | --- | --- | --- |",
    ]
    for arm in sweep["arms"]:
        for doc in arm["unstable_documents"][:12]:
            fields = ", ".join(doc["unstable_fields"][:6]) or "_formatting only_"
            lines.append(f"| {arm['temperature']} | `{doc['doc_id']}` | {doc['distinct_outputs']} | {fields} |")
    return "\n".join(lines) + "\n"
