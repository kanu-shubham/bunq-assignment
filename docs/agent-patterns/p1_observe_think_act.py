"""Prototype 1 — Observe / Think / Act.

The base loop every other pattern specialises. An agent that runs against a
changing world re-reads that world on every tick instead of trusting what it
remembered: the world is the source of truth, the transcript is not.

Scenario: an on-call agent watching a web service. Latency is climbing because a
bad release is live. The agent must notice, decide, act, and keep watching until
the service is healthy again — without acting twice on the same symptom.

    python3 p1_observe_think_act.py

What to point at in an interview:
  * `Observation` is built fresh each tick from `World.observe()`; the agent
    never infers current state from its own past actions.
  * The loop has a termination predicate (`obs.healthy`) *and* a tick budget.
    Every real loop needs both; "until done" is not a stop condition.
  * Acting is separated from observing, so a failed action shows up as the next
    observation rather than as an exception that kills the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llm import Message, Reply, ScriptedModel, assistant, say, think_and_call, transcript_text, user

# --------------------------------------------------------------------------
# The world. The agent cannot see this object, only observe() output.
# --------------------------------------------------------------------------


@dataclass
class World:
    p99_ms: int = 520          # the incident is already under way at tick 0
    error_rate: float = 0.03
    version: str = "v42"
    previous_version: str = "v41"
    rolled_back: bool = False
    scaled: bool = False
    tick: int = 0

    def step(self) -> None:
        """Time passes. A bad release keeps degrading until it is rolled back."""
        self.tick += 1
        if self.rolled_back:
            self.p99_ms = max(180, int(self.p99_ms * 0.55))
            self.error_rate = max(0.004, self.error_rate * 0.4)
        else:
            self.p99_ms = int(self.p99_ms * 1.6)
            self.error_rate = min(0.5, self.error_rate * 2.5)
        if self.scaled and not self.rolled_back:
            # Scaling buys a little headroom but does not fix a bad release.
            self.p99_ms = int(self.p99_ms * 0.9)

    def observe(self) -> "Observation":
        return Observation(
            tick=self.tick,
            p99_ms=self.p99_ms,
            error_rate=round(self.error_rate, 4),
            version=self.version,
            healthy=self.p99_ms < 400 and self.error_rate < 0.02,
        )

    # --- effects ----------------------------------------------------------
    def rollback(self, to: str) -> str:
        if to != self.previous_version:
            return f"refused: {to} is not the previous version ({self.previous_version})"
        self.rolled_back = True
        was, self.version = self.version, to
        return f"rolled back {was} -> {to}"

    def scale_out(self, replicas: int) -> str:
        self.scaled = True
        return f"scaled to {replicas} replicas"

    def page(self, who: str, why: str) -> str:
        return f"paged {who}: {why}"


@dataclass
class Observation:
    tick: int
    p99_ms: int
    error_rate: float
    version: str
    healthy: bool

    def render(self) -> str:
        return (
            f"tick={self.tick} p99={self.p99_ms}ms error_rate={self.error_rate} "
            f"version={self.version} healthy={self.healthy}"
        )


# --------------------------------------------------------------------------
# The agent's "think" step. Swap ScriptedModel for AnthropicModel to make this
# an LLM decision instead of a rule table; the loop around it does not change.
# --------------------------------------------------------------------------

SYSTEM = """You are an on-call agent for a web service.
Each turn you receive one fresh observation. Choose exactly one action:
  rollback(to)      - revert to the previous version
  scale_out(replicas)
  page(who, why)
  (or reply with text to do nothing this tick)
Never repeat an action that is already in effect."""


def policy(system: str, messages: list[Message]) -> Reply:
    history = transcript_text(messages)
    latest = messages[-1]["content"][0]["text"]
    p99 = int(latest.split("p99=")[1].split("ms")[0])
    healthy = "healthy=True" in latest

    if healthy:
        return say("Service is within SLO. Standing down.")
    if "did: rollback" in history:
        # The fix is already in flight. Re-issuing it would be an action taken
        # on a stale reading -- exactly what re-observing is meant to prevent.
        return say("Rollback in flight, waiting for latency to recover.")
    if p99 > 1000:
        return think_and_call(
            f"p99 {p99}ms is past the rollback threshold and v42 is the only change.",
            "rollback",
            to="v41",
        )
    if "did: page" not in history:
        return think_and_call(
            f"Out of SLO at {p99}ms but not catastrophic yet; get a human looking.",
            "page",
            who="release-owner",
            why=f"p99 {p99}ms after v42",
        )
    return say("Degrading but not yet past the rollback threshold; watching.")


TOOLS = {
    "rollback": lambda w, a: w.rollback(a["to"]),
    "scale_out": lambda w, a: w.scale_out(a["replicas"]),
    "page": lambda w, a: w.page(a["who"], a["why"]),
}


@dataclass
class Trace:
    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, observation: str, thought: str, action: str) -> None:
        self.rows.append((observation, thought, action))


def run(world: World, model, max_ticks: int = 10) -> Trace:
    """Observe -> Think -> Act, until healthy or out of budget."""
    trace = Trace()
    for _ in range(max_ticks):
        obs = world.observe()
        if obs.healthy and world.tick > 0:
            trace.add(obs.render(), "healthy", "stop")
            return trace

        # THINK. Only the current observation is passed as the new turn; the
        # transcript carries what the agent already did, not stale metrics.
        reply = model.respond(SYSTEM, _history(trace, obs))

        # ACT
        if reply.tool_calls:
            tc = reply.tool_calls[0]
            outcome = TOOLS[tc["name"]](world, tc["input"])
            action = f"{tc['name']}({tc['input']}) -> {outcome}"
        else:
            action = "noop"

        trace.add(obs.render(), reply.text or "(no comment)", action)
        world.step()
    return trace


def _history(trace: Trace, obs: Observation) -> list[Message]:
    """Past decisions + the one fresh observation."""
    msgs: list[Message] = []
    for observation, thought, action in trace.rows:
        msgs.append(user(observation))
        msgs.append(assistant(f"{thought} | did: {action}"))
    msgs.append(user(obs.render()))
    return msgs


if __name__ == "__main__":
    world = World()
    model = ScriptedModel(policy)
    trace = run(world, model)

    width = max(len(r[0]) for r in trace.rows)
    for observation, thought, action in trace.rows:
        print(f"OBSERVE {observation:<{width}}")
        print(f"  THINK {thought}")
        print(f"    ACT {action}\n")
    print(f"model calls: {model.calls}  final: {world.observe().render()}")
