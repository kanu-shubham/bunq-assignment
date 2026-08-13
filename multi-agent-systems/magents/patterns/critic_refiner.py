"""Critic + Refiner (generate -> critique -> revise, bounded).

The pattern that most reliably improves output quality, and the one most often
implemented wrongly. Three rules decide whether it works:

  1. **The critic must have something the generator lacks.** Separate context, a
     rubric, tool access, ground truth, or a different model. A critic that is
     the same model looking at the same context with the prompt "find problems"
     mostly produces plausible-sounding nitpicks — self-critique without new
     information has a well-documented ceiling.

  2. **The loop must be bounded and monotone.** Cap iterations, and stop when the
     score stops improving. Unbounded refine loops oscillate: v2 fixes A and
     breaks B, v3 fixes B and reintroduces A. Track the best version seen and
     return that, not the last one.

  3. **The critic must be able to say "ship it".** A critic rewarded for finding
     problems will always find problems. Give it an explicit pass threshold and
     an explicit "no blocking issues" output.

Where a verifier beats a critic: if correctness is *checkable* — tests pass, JSON
validates, the plan only touches allow-listed resources — run the check instead
of asking a model. Deterministic verification is cheaper, faster, and cannot be
argued with. Use an LLM critic only for the judgment residue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence

from ..llm import LLM

CRITIC_SYSTEM = """You review a draft against a rubric and decide whether it ships.

Report every issue you find, including ones you are uncertain about — a later
step filters by severity, so coverage matters more than precision here. For each
issue give: what is wrong, why it matters, and a concrete fix.

Then score the draft 0-10 against the rubric and decide.

Return JSON:
{"score": 0-10, "verdict": "pass" | "revise",
 "issues": [{"severity": "high"|"medium"|"low", "what": "...", "fix": "..."}],
 "summary": "one sentence"}

Return verdict "pass" with an empty issues list when the draft genuinely meets
the rubric. Do not invent issues to appear thorough."""

REFINER_SYSTEM = """Revise the draft to address the critique.

Fix what was raised. Do not rewrite parts that were not criticized, do not add
scope, and do not "improve" things nobody asked about — regressions in the
untouched parts are the main failure mode of a revision loop.

Output the revised draft only, with no commentary."""


@dataclass
class Critique:
    score: float
    verdict: str
    issues: list[dict]
    summary: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    @property
    def blocking(self) -> list[dict]:
        return [i for i in self.issues if i.get("severity") == "high"]


@dataclass
class Round:
    iteration: int
    draft: str
    critique: Critique


@dataclass
class RefineResult:
    output: str
    rounds: list[Round] = field(default_factory=list)
    stopped_because: str = ""

    @property
    def iterations(self) -> int:
        return len(self.rounds)

    @property
    def best_score(self) -> float:
        return max((r.critique.score for r in self.rounds), default=0.0)

    def trajectory(self) -> list[float]:
        return [r.critique.score for r in self.rounds]


class Verifier(Protocol):
    """Deterministic gate. Returns (ok, list of failures)."""

    def __call__(self, draft: str) -> tuple[bool, list[str]]: ...


class CriticRefiner:
    def __init__(
        self,
        llm: LLM,
        rubric: str,
        critic_llm: LLM | None = None,
        max_iterations: int = 3,
        pass_score: float = 8.0,
        min_improvement: float = 0.5,
        verifiers: Sequence[Verifier] = (),
        generator_system: str = "Produce the requested artifact. No preamble.",
    ):
        self.llm = llm
        # A distinct critic model is the cheapest way to satisfy rule 1 above.
        self.critic_llm = critic_llm or llm
        self.rubric = rubric
        self.max_iterations = max_iterations
        self.pass_score = pass_score
        self.min_improvement = min_improvement
        self.verifiers = list(verifiers)
        self.generator_system = generator_system

    def generate(self, task: str) -> str:
        return self.llm.complete(
            self.generator_system, [{"role": "user", "content": task}], max_tokens=4096
        ).text

    def critique(self, task: str, draft: str) -> Critique:
        # Deterministic verifiers run first and short-circuit the model call.
        # If tests fail, there is nothing for an LLM critic to weigh in on.
        hard_failures: list[str] = []
        for verify in self.verifiers:
            ok, failures = verify(draft)
            if not ok:
                hard_failures.extend(failures)
        if hard_failures:
            return Critique(
                score=0.0,
                verdict="revise",
                issues=[
                    {"severity": "high", "what": f, "fix": "make the check pass"}
                    for f in hard_failures
                ],
                summary=f"{len(hard_failures)} deterministic check(s) failed",
            )

        out = self.critic_llm.complete(
            CRITIC_SYSTEM,
            [
                {
                    "role": "user",
                    "content": f"Rubric:\n{self.rubric}\n\nTask:\n{task}\n\nDraft:\n{draft}",
                }
            ],
            max_tokens=2048,
            effort="high",
        )
        payload = out.json({}) or {}
        return Critique(
            score=float(payload.get("score", 0.0)),
            verdict=str(payload.get("verdict", "revise")),
            issues=list(payload.get("issues", [])),
            summary=str(payload.get("summary", "")),
        )

    def refine(self, task: str, draft: str, critique: Critique) -> str:
        issues = "\n".join(
            f"- [{i.get('severity','?')}] {i.get('what','')} -> {i.get('fix','')}"
            for i in critique.issues
        )
        return self.llm.complete(
            REFINER_SYSTEM,
            [{"role": "user", "content": f"Task:\n{task}\n\nDraft:\n{draft}\n\nIssues:\n{issues}"}],
            max_tokens=4096,
        ).text

    def run(self, task: str, draft: str | None = None) -> RefineResult:
        current = draft if draft is not None else self.generate(task)
        rounds: list[Round] = []
        best_draft, best_score = current, -1.0
        reason = "max_iterations"

        for i in range(self.max_iterations):
            critique = self.critique(task, current)
            rounds.append(Round(i, current, critique))

            if critique.score > best_score:
                best_draft, best_score = current, critique.score

            if critique.passed and critique.score >= self.pass_score:
                reason = "passed"
                break

            # Convergence guard: if the last revision gained less than
            # `min_improvement`, further rounds are burning tokens for noise.
            if i > 0:
                gain = critique.score - rounds[-2].critique.score
                if gain < self.min_improvement:
                    reason = f"converged (gain {gain:+.1f} < {self.min_improvement})"
                    break

            if i == self.max_iterations - 1:
                break
            current = self.refine(task, current, critique)

        # Return the best draft seen, not the last — refinement is not monotone.
        return RefineResult(best_draft, rounds, reason)


# -- reusable deterministic verifiers --------------------------------------
def json_verifier(required_keys: Sequence[str] = ()) -> Verifier:
    def _verify(draft: str) -> tuple[bool, list[str]]:
        from ..llm import extract_json

        data = extract_json(draft)
        if data is None:
            return False, ["output is not valid JSON"]
        missing = [k for k in required_keys if k not in data]
        return (not missing), [f"missing required key {k!r}" for k in missing]

    return _verify


def predicate_verifier(name: str, fn: Callable[[str], bool]) -> Verifier:
    def _verify(draft: str) -> tuple[bool, list[str]]:
        return (True, []) if fn(draft) else (False, [f"failed check: {name}"])

    return _verify
