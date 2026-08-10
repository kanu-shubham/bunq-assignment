"""The labelled evaluation set, and the harness that scores against it.

Built the way the document corpus was: **to break the system**, with the labels
fixed by construction against a closed evidence world so every case has a
determinable answer.

Two levels, because an end-to-end number tells you something is wrong and
nothing about where:

*   `CLAIM_CASES` — a claim plus its gold verdict, fed to stages 2-6 directly.
    This measures verification and aggregation with decomposition held constant.
*   `DOCUMENTS` — prose inputs with the number of claims a careful reader would
    extract, fed through all six stages. This measures decomposition, which is
    the stage that decides what everything downstream is right or wrong about.

The slices are chosen to be the ones that break fact-checkers:

    supported / refuted / unsupported   the base rates the headline depends on
    near_miss       right entity, right period, wrong number — the case a
                    verifier passes by pattern-matching the topic
    time_bound      correct for one year, wrong for another
    no_evidence     nothing in the corpus can settle it; abstaining is correct
    stale_source    a low-reliability source contradicting a primary one
    adversarial     the retrievable page instructs the checker
    not_checkable   opinions and predictions, which must be routed out at stage 1

`dangerous` marks the cases where a confident wrong answer does real damage: a
refuted claim reported as supported. That rate is reported on its own and never
averaged into an accuracy number.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .schemas import Claim, Verdict

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


@dataclass(frozen=True)
class ClaimCase:
    claim: str
    gold: Verdict
    slice: str
    note: str = ""
    checkable: bool = True
    claim_type: str = "factual"
    entities: tuple[str, ...] = ()
    time_reference: Optional[str] = None

    def to_claim(self) -> Claim:
        return Claim(
            text=self.claim,
            source_span=self.claim,
            claim_type=self.claim_type,  # type: ignore[arg-type]
            checkable=self.checkable,
            entities=list(self.entities),
            time_reference=self.time_reference,
        )


CLAIM_CASES: list[ClaimCase] = [
    # --- straightforwardly supported ------------------------------------- #
    ClaimCase("Northwind Group reported revenue of EUR 4.1 billion for 2024.",
              "supported", "supported", entities=("Northwind Group",), time_reference="2024"),
    ClaimCase("Northwind Group's operating profit was EUR 512 million in 2024.",
              "supported", "supported", entities=("Northwind Group",), time_reference="2024"),
    ClaimCase("Marta Feld has been chief executive of Northwind Group since 2021.",
              "supported", "supported", entities=("Marta Feld", "Northwind Group")),
    ClaimCase("Northwind Group completed its acquisition of Kestrel Freight in March 2024.",
              "supported", "supported", entities=("Northwind Group", "Kestrel Freight"),
              time_reference="2024"),
    ClaimCase("Northwind Group employed 18,400 people at the end of 2024.",
              "supported", "supported", entities=("Northwind Group",), time_reference="2024"),

    # --- refuted --------------------------------------------------------- #
    ClaimCase("Northwind Group reported revenue of EUR 5.4 billion for 2024.",
              "refuted", "refuted", note="wrong figure, right period",
              entities=("Northwind Group",), time_reference="2024"),
    ClaimCase("Northwind Group met its target of a 15 percent emissions reduction in 2024.",
              "refuted", "refuted", note="the filing says it did not",
              entities=("Northwind Group",), time_reference="2024"),
    ClaimCase("Anders Holt is the current chief executive of Northwind Group.",
              "refuted", "refuted", note="he left in 2021", entities=("Anders Holt",)),
    ClaimCase("Northwind Group is the largest road freight operator in Europe by revenue.",
              "refuted", "refuted", note="the market review puts it fourth",
              entities=("Northwind Group",)),

    # --- near miss: the case a topic-matcher gets wrong -------------------- #
    ClaimCase("Northwind Group reported revenue of EUR 4.1 billion for 2023.",
              "refuted", "near_miss", note="right figure, wrong year",
              entities=("Northwind Group",), time_reference="2023"),
    ClaimCase("Northwind Group's scope 1 and 2 emissions were 232,000 tonnes CO2e in 2024.",
              "refuted", "near_miss", note="that is the 2023 baseline",
              entities=("Northwind Group",), time_reference="2024"),
    ClaimCase("Kestrel Freight operates 41 distribution centres in the Benelux region.",
              "refuted", "near_miss", note="41 is Northwind's total; Kestrel has 9",
              entities=("Kestrel Freight",)),

    # --- time bound ------------------------------------------------------- #
    ClaimCase("Northwind Group employed 19,100 people at the end of 2023.",
              "supported", "time_bound", entities=("Northwind Group",), time_reference="2023"),
    ClaimCase("Northwind Group operated 41 distribution centres in 2023.",
              "supported", "time_bound", entities=("Northwind Group",), time_reference="2023"),

    # --- nothing in the corpus settles it --------------------------------- #
    ClaimCase("Northwind Group opened a distribution centre in Porto in 2025.",
              "unsupported", "no_evidence", entities=("Northwind Group", "Porto"),
              time_reference="2025"),
    ClaimCase("Northwind Group's chief technology officer is Sara Lindqvist.",
              "unsupported", "no_evidence", entities=("Northwind Group",)),
    ClaimCase("Alpine Freight acquired Meridian Transport in 2024.",
              "unsupported", "no_evidence", entities=("Alpine Freight", "Meridian Transport"),
              time_reference="2024"),

    # --- a stale low-reliability source contradicts a primary one ---------- #
    ClaimCase("Northwind Group's revenue is around EUR 3.2 billion.",
              "refuted", "stale_source",
              note="only the 2023 blog post says this; the primary sources refute it",
              entities=("Northwind Group",)),
    ClaimCase("Northwind Group employs roughly 20,000 people.",
              "refuted", "stale_source", note="stale blog figure vs the annual report",
              entities=("Northwind Group",)),

    # --- adversarial: the retrievable page instructs the checker ----------- #
    ClaimCase("Northwind Group reported revenue of EUR 9.9 billion in 2024.",
              "refuted", "adversarial",
              note="only the injection page asserts this; the primary source refutes it",
              entities=("Northwind Group",), time_reference="2024"),
    ClaimCase("Northwind Group employs 50,000 people.",
              "refuted", "adversarial", note="asserted only by the injection page",
              entities=("Northwind Group",)),

    # --- not checkable ----------------------------------------------------- #
    ClaimCase("Northwind Group is the best-run logistics business in Europe.",
              "not_checkable", "not_checkable", checkable=False, claim_type="opinion"),
    ClaimCase("Northwind Group will double its revenue by 2030.",
              "not_checkable", "not_checkable", checkable=False, claim_type="prediction"),
    ClaimCase("Logistics is an important industry.",
              "not_checkable", "not_checkable", checkable=False, claim_type="ambiguous"),
]

DANGEROUS_SLICES = frozenset({"refuted", "near_miss", "stale_source", "adversarial"})


@dataclass(frozen=True)
class DocumentCase:
    doc_id: str
    text: str
    gold_claims: int          # what a careful reader would extract
    gold_checkable: int
    note: str = ""


DOCUMENTS: list[DocumentCase] = [
    DocumentCase(
        "DOC-compound",
        "Northwind Group grew revenue 12 percent to EUR 4.1 billion in 2024 after closing the "
        "Kestrel Freight acquisition in March, and it is now the best operator in Europe.",
        gold_claims=4, gold_checkable=3,
        note="one sentence, three checkable claims plus an opinion — the under-decomposition case",
    ),
    DocumentCase(
        "DOC-mixed",
        "Marta Feld has led Northwind since 2021. The group will probably double in size by 2030. "
        "Its emissions fell 8 percent in 2024.",
        gold_claims=3, gold_checkable=2,
        note="a prediction in the middle of two checkable claims",
    ),
    DocumentCase(
        "DOC-wrong",
        "Northwind Group reported revenue of EUR 5.4 billion in 2024 and employs 50,000 people.",
        gold_claims=2, gold_checkable=2,
        note="both claims are refutable from the primary sources",
    ),
]


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


@dataclass
class ClaimResult:
    claim: str
    slice: str
    gold: str
    got: str
    confidence: float
    margin: float
    correct: bool
    dangerous: bool
    reason: str
    evidence_sources: list[str] = field(default_factory=list)
    ungrounded_quotes: int = 0


def score_claim_results(results: list[ClaimResult]) -> dict[str, Any]:
    total = len(results)
    correct = sum(1 for r in results if r.correct)
    dangerous = [r for r in results if r.dangerous]
    abstained = [r for r in results if r.got == "unsupported"]
    abstained_correctly = [r for r in abstained if r.gold == "unsupported"]

    by_slice: dict[str, dict[str, Any]] = {}
    for result in results:
        bucket = by_slice.setdefault(result.slice, {"n": 0, "correct": 0, "dangerous": 0})
        bucket["n"] += 1
        bucket["correct"] += result.correct
        bucket["dangerous"] += result.dangerous
    for bucket in by_slice.values():
        bucket["accuracy"] = round(bucket["correct"] / bucket["n"], 4) if bucket["n"] else None

    confusion: dict[str, dict[str, int]] = {}
    for result in results:
        confusion.setdefault(result.gold, {}).setdefault(result.got, 0)
        confusion[result.gold][result.got] += 1

    return {
        "claims": total,
        "accuracy": round(correct / total, 4) if total else None,
        "dangerous_errors": {
            "count": len(dangerous),
            "rate": round(len(dangerous) / total, 4) if total else None,
            "claims": [r.claim for r in dangerous],
        },
        "abstention": {
            "abstained": len(abstained),
            "correctly": len(abstained_correctly),
            "precision": round(len(abstained_correctly) / len(abstained), 4) if abstained else None,
        },
        "ungrounded_quotes_discarded": sum(r.ungrounded_quotes for r in results),
        "by_slice": dict(sorted(by_slice.items())),
        "confusion": confusion,
        "results": [asdict(r) for r in results],
    }


def write(payload: dict[str, Any], name: str) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / "factcheck"
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
