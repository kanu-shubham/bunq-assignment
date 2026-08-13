"""Orchestrator + Specialist (a.k.a. supervisor / manager-worker).

One coordinator decomposes a task, routes each piece to a narrow specialist, and
synthesizes the results. This is the default multi-agent topology, and the one
worth defending in an interview because the honest answer includes when *not* to
use it.

Why it works
  * Context isolation. Each specialist gets a fresh window containing only its
    sub-task. The orchestrator's context holds plans and conclusions, not the
    raw material — so a task that would blow one agent's window fits comfortably.
  * Narrow prompts + narrow tool sets. A specialist with 4 tools picks correctly
    far more often than a generalist with 40.
  * Cost tiering. Reading-heavy specialists run on a cheap model; the
    orchestrator's planning and synthesis run on the expensive one.
  * Parallelism. Independent sub-tasks run concurrently — the latency win.

What it costs
  * Every handoff is a re-briefing. Specialists share no conversation history,
    so anything they need must be in the task spec. Under-specified handoffs are
    the #1 cause of "the sub-agent did the wrong thing".
  * Round trips. A sub-task you could finish in two tool calls is cheaper done
    inline. Delegate for isolation or parallelism, not for tidiness.
  * Error amplification. A confidently wrong specialist result reaches the
    synthesis step stripped of the caveats it had in its own reasoning — hence
    `confidence` on every result here.

When to reach for something else
  * Sequential dependency with no fan-out -> a pipeline, not an orchestrator.
  * Sub-tasks needing to negotiate -> peer messaging or a blackboard.
  * One well-scoped task -> a single agent. Most "multi-agent" systems should be.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..llm import LLM, Usage

PLANNER_SYSTEM = """You decompose a task into sub-tasks for specialist agents.

Available specialists:
{roster}

Rules:
- Each sub-task must be self-contained. The specialist sees NOTHING of this
  conversation — include every path, identifier, and constraint it needs.
- State the expected output shape for each sub-task.
- Mark sub-tasks that can run in parallel with the same `wave` number.
- Do not create a sub-task you could answer directly. Fewer, larger sub-tasks
  beat many small ones.

Return JSON:
{{"subtasks": [{{"id": "s1", "specialist": "name", "wave": 0,
                 "task": "...", "expects": "..."}}]}}"""

SYNTH_SYSTEM = """You are the orchestrator. Combine specialist reports into one answer.

Specialists worked in isolation and may disagree or overlap. Where they conflict,
say so explicitly and state which you trust and why — do not average them into
a bland consensus. Where evidence is thin, say that too.

Lead with the answer; supporting detail after."""


@dataclass
class Specialist:
    name: str
    description: str  # the orchestrator routes on THIS — write it for the model
    system: str
    handler: Callable[[str, LLM], str] | None = None
    llm: LLM | None = None  # per-specialist model tiering
    effort: str = "medium"
    max_tokens: int = 2048

    def run(self, task: str, default_llm: LLM) -> str:
        llm = self.llm or default_llm
        if self.handler is not None:
            return self.handler(task, llm)
        return llm.complete(
            self.system,
            [{"role": "user", "content": task}],
            max_tokens=self.max_tokens,
            effort=self.effort,
        ).text


@dataclass
class SubTask:
    id: str
    specialist: str
    task: str
    wave: int = 0
    expects: str = ""


@dataclass
class SpecialistResult:
    subtask_id: str
    specialist: str
    output: str
    error: str | None = None
    confidence: float = 0.8

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class OrchestrationResult:
    answer: str
    plan: list[SubTask]
    results: list[SpecialistResult]
    usage: Usage = field(default_factory=Usage)

    @property
    def failures(self) -> list[SpecialistResult]:
        return [r for r in self.results if not r.ok]


class Orchestrator:
    def __init__(
        self,
        llm: LLM,
        specialists: Sequence[Specialist],
        max_parallel: int = 5,
        max_subtasks: int = 12,
    ):
        self.llm = llm
        self.specialists = {s.name: s for s in specialists}
        self.max_parallel = max_parallel
        self.max_subtasks = max_subtasks

    # -- plan --------------------------------------------------------------
    def plan(self, task: str) -> list[SubTask]:
        roster = "\n".join(f"- {s.name}: {s.description}" for s in self.specialists.values())
        out = self.llm.complete(
            PLANNER_SYSTEM.format(roster=roster),
            [{"role": "user", "content": task}],
            max_tokens=2048,
            effort="high",  # planning is the intelligence-sensitive step
        )
        payload = out.json({"subtasks": []}) or {"subtasks": []}
        plan: list[SubTask] = []
        for raw in payload.get("subtasks", [])[: self.max_subtasks]:
            name = raw.get("specialist")
            if name not in self.specialists:
                # A plan naming a specialist that does not exist is a routing
                # failure, not a crash — drop it and let synthesis note the gap.
                continue
            plan.append(
                SubTask(
                    id=str(raw.get("id") or f"s{len(plan)}"),
                    specialist=name,
                    task=str(raw.get("task", "")),
                    wave=int(raw.get("wave", 0)),
                    expects=str(raw.get("expects", "")),
                )
            )
        return plan

    # -- dispatch ----------------------------------------------------------
    def dispatch(self, plan: Sequence[SubTask]) -> list[SpecialistResult]:
        """Run each wave in parallel, waves in order.

        Failure policy is partial-success: one specialist blowing up degrades the
        answer, it does not fail the task. Synthesis is told what is missing.
        """
        results: list[SpecialistResult] = []
        for wave in sorted({st.wave for st in plan}):
            batch = [st for st in plan if st.wave == wave]
            with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(batch))) as pool:
                futures = {pool.submit(self._run_one, st): st for st in batch}
                for future, subtask in futures.items():
                    try:
                        results.append(future.result())
                    except Exception as exc:  # noqa: BLE001 - isolate specialist failures
                        results.append(
                            SpecialistResult(
                                subtask.id, subtask.specialist, "", error=str(exc), confidence=0.0
                            )
                        )
        return results

    def _run_one(self, subtask: SubTask) -> SpecialistResult:
        specialist = self.specialists[subtask.specialist]
        brief = subtask.task
        if subtask.expects:
            brief += f"\n\nReturn: {subtask.expects}"
        output = specialist.run(brief, self.llm)
        return SpecialistResult(subtask.id, subtask.specialist, output)

    # -- synthesize --------------------------------------------------------
    def synthesize(self, task: str, results: Sequence[SpecialistResult]) -> str:
        report = json.dumps(
            [
                {
                    "specialist": r.specialist,
                    "status": "ok" if r.ok else "failed",
                    "output": r.output or r.error,
                }
                for r in results
            ],
            indent=2,
        )
        return self.llm.complete(
            SYNTH_SYSTEM,
            [{"role": "user", "content": f"Original task:\n{task}\n\nReports:\n{report}"}],
            max_tokens=4096,
            effort="high",
        ).text

    def run(self, task: str) -> OrchestrationResult:
        plan = self.plan(task)
        if not plan:
            # No decomposition needed (or planning failed) — answer directly
            # rather than returning an empty result.
            answer = self.llm.complete(
                "Answer the task directly.", [{"role": "user", "content": task}]
            ).text
            return OrchestrationResult(answer, [], [])
        results = self.dispatch(plan)
        return OrchestrationResult(self.synthesize(task, results), plan, results)


def static_plan(*subtasks: tuple[str, str, int]) -> list[SubTask]:
    """Hand-written plan: (specialist, task, wave).

    Worth stating plainly — if the decomposition is known in advance, do not pay
    a model call to rediscover it every run. Model-generated plans are for
    genuinely open-ended tasks.
    """
    return [
        SubTask(id=f"s{i}", specialist=name, task=task, wave=wave)
        for i, (name, task, wave) in enumerate(subtasks)
    ]
