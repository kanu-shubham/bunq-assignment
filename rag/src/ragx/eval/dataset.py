"""Golden set loading.

The eval set is JSONL, one case per line, version-controlled next to the code.
It deliberately mixes four kinds of case, because a set made only of the first
kind will happily green-light a system that hallucinates:

  * **answerable** — a fact that exists in exactly one place
  * **multi-hop** — needs two or more documents combined
  * **unanswerable** — the corpus genuinely does not say; the correct behaviour
    is abstention, and this is the only way to measure it
  * **permission-scoped** — answerable for one group, must abstain for another

`relevant_doc_ids` is the durable label. `relevant_chunk_ids` is convenient but
brittle: chunk ids change when the chunker changes, which is exactly the moment
you most want the eval to stay comparable. Doc-level recall is the metric to
track across chunker changes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from ..types import EvalCase, Visibility


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        cases.append(_from_json(json.loads(line)))
    return cases


def _from_json(row: dict) -> EvalCase:
    return EvalCase(
        case_id=row["case_id"],
        question=row["question"],
        relevant_chunk_ids=tuple(row.get("relevant_chunk_ids", ())),
        relevant_doc_ids=tuple(row.get("relevant_doc_ids", ())),
        reference_answer=row.get("reference_answer"),
        unanswerable=bool(row.get("unanswerable", False)),
        principal_groups=frozenset(row.get("principal_groups", ())),
        principal_max_visibility=Visibility(
            row.get("principal_max_visibility", Visibility.INTERNAL.value)
        ),
        tags=tuple(row.get("tags", ())),
    )


def write_cases(path: Path, cases: Iterator[EvalCase]) -> None:
    lines = []
    for case in cases:
        lines.append(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "question": case.question,
                    "relevant_doc_ids": list(case.relevant_doc_ids),
                    "relevant_chunk_ids": list(case.relevant_chunk_ids),
                    "reference_answer": case.reference_answer,
                    "unanswerable": case.unanswerable,
                    "principal_groups": sorted(case.principal_groups),
                    "principal_max_visibility": case.principal_max_visibility.value,
                    "tags": list(case.tags),
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
