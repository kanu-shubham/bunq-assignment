"""Mixture of Agents (MoA).

N proposers answer the *same* question independently; an aggregator synthesizes
one answer. Optionally stack layers: each layer's proposers see the previous
layer's proposals and revise.

    layer 0:  P1  P2  P3      (independent, same prompt, different models/personas)
                 \  |  /
    layer 1:  P1' P2' P3'     (each sees all layer-0 proposals)
                 \  |  /
    aggregator:   final answer

Distinguish it from the neighbours, because interviewers probe this:

  MoA vs orchestrator-specialist — specialists answer *different* sub-questions
    (decomposition, for coverage). MoA proposers answer the *same* question
    (redundancy, for reliability). Fan-out looks identical; the intent is
    opposite.
  MoA vs self-consistency — self-consistency samples one model N times and takes
    a majority vote. MoA uses heterogeneous proposers and a *synthesizing*
    aggregator, so it can combine complementary partial answers rather than
    picking one.
  MoA vs ensembling in classical ML — same variance-reduction intuition, but the
    aggregator is a model that reads the proposals, so it can also notice that
    all three proposers made the same mistake.

Cost is the honest objection: N+1 calls for one answer. It earns that when
errors are independent and expensive — diagnosis, risk assessment, anything
where a confident wrong answer costs more than the extra tokens. It does not
earn it on routine generation.

The failure mode to design against is **correlated error**: three proposers on
the same model with the same prompt produce three copies of the same mistake and
the aggregator reads that as consensus. Force diversity — different models,
different personas, different evidence — and make the aggregator report
agreement explicitly so you can tell consensus from groupthink.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..llm import LLM

AGGREGATOR_SYSTEM = """You synthesize several independent answers to the same question.

Do not simply pick one, and do not average them into something vaguer than any
input. Instead:
- Keep claims that multiple proposers reached independently, and say so.
- Where they conflict, judge on the evidence given and state which you took.
- Keep a claim only one proposer made if its reasoning holds up; note it as
  single-sourced.
- If they agree but the shared reasoning looks wrong, say that — unanimity is
  not evidence.

Lead with the answer. Then a short "agreement" note: what was unanimous, what was
contested, what is single-sourced."""


@dataclass
class Proposer:
    name: str
    system: str
    llm: LLM | None = None
    effort: str = "medium"
    max_tokens: int = 2048
    weight: float = 1.0  # for weighted voting


@dataclass
class Proposal:
    proposer: str
    text: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class MoAResult:
    answer: str
    proposals: list[Proposal] = field(default_factory=list)
    layers: list[list[Proposal]] = field(default_factory=list)
    agreement: float = 0.0  # 0..1 lexical overlap across proposals

    @property
    def n_ok(self) -> int:
        return sum(1 for p in self.proposals if p.ok)


class MixtureOfAgents:
    def __init__(
        self,
        aggregator: LLM,
        proposers: Sequence[Proposer],
        layers: int = 1,
        max_parallel: int = 6,
        aggregator_system: str = AGGREGATOR_SYSTEM,
    ):
        if not proposers:
            raise ValueError("MoA needs at least one proposer")
        self.aggregator = aggregator
        self.proposers = list(proposers)
        self.layers = max(1, layers)
        self.max_parallel = max_parallel
        self.aggregator_system = aggregator_system

    def _ask(self, proposer: Proposer, prompt: str) -> Proposal:
        llm = proposer.llm or self.aggregator
        try:
            out = llm.complete(
                proposer.system,
                [{"role": "user", "content": prompt}],
                max_tokens=proposer.max_tokens,
                effort=proposer.effort,
            )
            return Proposal(proposer.name, out.text)
        except Exception as exc:  # noqa: BLE001 - one proposer failing is survivable
            return Proposal(proposer.name, "", error=str(exc))

    def _layer(self, prompt: str) -> list[Proposal]:
        with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(self.proposers))) as pool:
            futures = [pool.submit(self._ask, p, prompt) for p in self.proposers]
            return [f.result() for f in futures]

    def run(self, question: str) -> MoAResult:
        prompt = question
        all_layers: list[list[Proposal]] = []
        proposals: list[Proposal] = []

        for layer in range(self.layers):
            proposals = self._layer(prompt)
            all_layers.append(proposals)
            if layer < self.layers - 1:
                # Next layer sees peers' answers. Anonymized on purpose: labelling
                # them "the senior engineer's answer" makes proposers defer
                # instead of reasoning.
                peer_view = "\n\n".join(
                    f"[proposal {i + 1}]\n{p.text}" for i, p in enumerate(proposals) if p.ok
                )
                prompt = (
                    f"{question}\n\nOther independent answers to the same question "
                    f"are below. Use anything correct, correct anything wrong, and "
                    f"give your own final answer.\n\n{peer_view}"
                )

        usable = [p for p in proposals if p.ok and p.text.strip()]
        if not usable:
            return MoAResult("All proposers failed.", proposals, all_layers, 0.0)

        answer = self.aggregator.complete(
            self.aggregator_system,
            [
                {
                    "role": "user",
                    "content": f"Question:\n{question}\n\n"
                    + "\n\n".join(f"[{p.proposer}]\n{p.text}" for p in usable),
                }
            ],
            max_tokens=4096,
            effort="high",
        ).text
        return MoAResult(answer, proposals, all_layers, agreement_score(usable))

    # -- cheap aggregators that need no model call -------------------------
    def vote(self, question: str, extract: Callable[[str], str]) -> tuple[str, dict[str, float]]:
        """Weighted majority vote over an extracted label.

        Use this instead of an LLM aggregator when the answer space is discrete
        (a severity, a root-cause category, yes/no). It is free, deterministic,
        and auditable — three properties an LLM aggregator does not have.
        """
        proposals = self._layer(question)
        tally: Counter[str] = Counter()
        weights = {p.name: p.weight for p in self.proposers}
        for proposal in proposals:
            if not proposal.ok:
                continue
            label = extract(proposal.text)
            if label:
                tally[label] += weights.get(proposal.proposer, 1.0)
        if not tally:
            return "", {}
        total = sum(tally.values())
        return tally.most_common(1)[0][0], {k: v / total for k, v in tally.items()}


def agreement_score(proposals: Sequence[Proposal]) -> float:
    """Mean pairwise Jaccard overlap over content words.

    Crude, and that is fine — it is a *diversity alarm*, not a metric. A score
    near 1.0 means your proposers are not independent and MoA is buying you
    nothing but latency. Near 0.0 means they disagree wildly and the aggregator
    is doing real work (or your prompts are inconsistent).
    """
    texts = [set(re.findall(r"[a-z]{4,}", p.text.lower())) for p in proposals if p.ok]
    texts = [t for t in texts if t]
    if len(texts) < 2:
        return 1.0
    scores = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            union = texts[i] | texts[j]
            if union:
                scores.append(len(texts[i] & texts[j]) / len(union))
    return sum(scores) / len(scores) if scores else 0.0
