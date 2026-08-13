"""A minimal Pregel-style state graph — the execution substrate every
multi-agent framework (LangGraph, AutoGen's GroupChat, CrewAI's Flow) reduces to.

Why hand-roll it instead of importing LangGraph? Because in an interview you get
asked *how it works*, not how to import it. Every concept here maps 1:1 onto the
LangGraph API, so the vocabulary transfers:

    this module              LangGraph
    ----------------------   ---------------------------------
    StateGraph               langgraph.graph.StateGraph
    add_node / add_edge      identical
    add_conditional_edges    identical
    Reducers                 Annotated[list, operator.add] in the state TypedDict
    Checkpointer             langgraph.checkpoint.base.BaseCheckpointSaver
    interrupt_before         compile(interrupt_before=[...])
    END                      langgraph.graph.END
    superstep                Pregel step / "tick"

Execution model: bulk-synchronous parallel (BSP). Each superstep runs every node
in the current frontier — concurrently if more than one — collects their *partial*
state updates, merges them through per-key reducers, then computes the next
frontier. Nodes never mutate shared state directly; that is what makes fan-out
deterministic and what makes checkpoint/resume possible.
"""

from __future__ import annotations

import copy
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

START = "__start__"
END = "__end__"

State = dict[str, Any]
NodeFn = Callable[[State], State | None]
Reducer = Callable[[Any, Any], Any]
Router = Callable[[State], str | list[str]]


class GraphError(RuntimeError):
    pass


class RecursionLimit(GraphError):
    """Raised when a graph exceeds its superstep budget.

    This is the cheap, always-on guard against the most common multi-agent
    failure: two nodes routing to each other forever. See coordination/locks.py
    for the harder liveness problems.
    """


class Interrupt(GraphError):
    """Raised when execution pauses at a gate. State is already checkpointed."""

    def __init__(self, node: str, thread_id: str, state: State):
        super().__init__(f"interrupted before node {node!r} on thread {thread_id!r}")
        self.node = node
        self.thread_id = thread_id
        self.state = state


# --------------------------------------------------------------------------
# Reducers: how two writes to the same state key get merged
# --------------------------------------------------------------------------
def last_write_wins(old: Any, new: Any) -> Any:
    return new


def append(old: Any, new: Any) -> list:
    base = list(old or [])
    base.extend(new if isinstance(new, list) else [new])
    return base


def add(old: Any, new: Any) -> Any:
    return (old or 0) + new


def merge_dict(old: Any, new: Any) -> dict:
    out = dict(old or {})
    out.update(new or {})
    return out


def unique_append(key: Callable[[Any], Any] = lambda x: x) -> Reducer:
    """Append with dedupe — for findings that several agents may report twice."""

    def _reduce(old: Any, new: Any) -> list:
        seen, out = set(), []
        for item in list(old or []) + (new if isinstance(new, list) else [new]):
            k = key(item)
            if k in seen:
                continue
            seen.add(k)
            out.append(item)
        return out

    return _reduce


# --------------------------------------------------------------------------
# Checkpointing
# --------------------------------------------------------------------------
@dataclass
class Checkpoint:
    thread_id: str
    step: int
    state: State
    frontier: list[str]
    created_at: float = field(default_factory=time.time)


class Checkpointer(Protocol):
    def put(self, cp: Checkpoint) -> None: ...
    def latest(self, thread_id: str) -> Checkpoint | None: ...
    def history(self, thread_id: str) -> list[Checkpoint]: ...


class InMemoryCheckpointer:
    """Reference implementation. Swap for SQLite/Postgres/Redis in production —
    the point of the interface is that durability is a deployment concern, not
    an agent concern."""

    def __init__(self) -> None:
        self._log: dict[str, list[Checkpoint]] = {}

    def put(self, cp: Checkpoint) -> None:
        self._log.setdefault(cp.thread_id, []).append(cp)

    def latest(self, thread_id: str) -> Checkpoint | None:
        log = self._log.get(thread_id)
        return log[-1] if log else None

    def history(self, thread_id: str) -> list[Checkpoint]:
        return list(self._log.get(thread_id, []))


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------
@dataclass
class _Branch:
    router: Router
    mapping: dict[str, str] | None
    targets: tuple[str, ...] = ()  # declared for validation + rendering


class StateGraph:
    def __init__(self, reducers: dict[str, Reducer] | None = None):
        self.nodes: dict[str, NodeFn] = {}
        self.edges: dict[str, list[str]] = {}
        self.branches: dict[str, _Branch] = {}
        self.reducers: dict[str, Reducer] = dict(reducers or {})
        self.entry: str | None = None

    def add_node(self, name: str, fn: NodeFn) -> "StateGraph":
        if name in (START, END):
            raise GraphError(f"{name!r} is reserved")
        if name in self.nodes:
            raise GraphError(f"duplicate node {name!r}")
        self.nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        self.edges.setdefault(src, []).append(dst)
        return self

    def add_conditional_edges(
        self,
        src: str,
        router: Router,
        mapping: dict[str, str] | None = None,
        targets: Iterable[str] = (),
    ) -> "StateGraph":
        """`router(state)` returns a key (or list of keys). With `mapping`, the
        key is looked up to get the node name; without it, the key *is* the node
        name. Returning a list is how you fan out to parallel specialists.

        `targets` declares the reachable set when no `mapping` is given. It is
        optional but worth supplying: it lets `compile()` catch a router that can
        return a node name you never defined, and it makes `mermaid()` render the
        real topology instead of a question mark.
        """
        self.branches[src] = _Branch(router, mapping, tuple(targets))
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self.entry = name
        return self

    def compile(
        self,
        checkpointer: Checkpointer | None = None,
        interrupt_before: Iterable[str] = (),
        recursion_limit: int = 50,
        max_parallelism: int = 8,
    ) -> "CompiledGraph":
        if self.entry is None:
            raise GraphError("no entry point set")
        self._validate()
        return CompiledGraph(
            self,
            checkpointer=checkpointer,
            interrupt_before=set(interrupt_before),
            recursion_limit=recursion_limit,
            max_parallelism=max_parallelism,
        )

    def _validate(self) -> None:
        known = set(self.nodes) | {END}
        for src, dsts in self.edges.items():
            if src not in self.nodes:
                raise GraphError(f"edge from unknown node {src!r}")
            for dst in dsts:
                if dst not in known:
                    raise GraphError(f"edge to unknown node {dst!r}")
        for src, branch in self.branches.items():
            if src not in self.nodes:
                raise GraphError(f"branch from unknown node {src!r}")
            declared = list((branch.mapping or {}).values()) + list(branch.targets)
            for dst in declared:
                if dst not in known:
                    raise GraphError(f"branch to unknown node {dst!r}")
        if self.entry not in self.nodes:
            raise GraphError(f"entry point {self.entry!r} is not a node")
        # Every node must be able to reach END, or the graph can only ever
        # terminate by hitting the recursion limit.
        for name in self.nodes:
            if name not in self.edges and name not in self.branches:
                raise GraphError(f"node {name!r} has no outgoing edge; add one to END")


@dataclass
class StepTrace:
    step: int
    nodes: list[str]
    duration_ms: float
    updates: dict[str, list[str]]  # node -> keys it wrote


class CompiledGraph:
    def __init__(
        self,
        graph: StateGraph,
        checkpointer: Checkpointer | None,
        interrupt_before: set[str],
        recursion_limit: int,
        max_parallelism: int,
    ):
        self.g = graph
        self.checkpointer = checkpointer
        self.interrupt_before = interrupt_before
        self.recursion_limit = recursion_limit
        self.max_parallelism = max_parallelism
        self.trace: list[StepTrace] = []

    # -- public API --------------------------------------------------------
    def invoke(self, state: State, thread_id: str | None = None) -> State:
        thread_id = thread_id or uuid.uuid4().hex[:12]
        return self._run(copy.deepcopy(state), [self.g.entry], thread_id, step=0)

    def resume(self, thread_id: str, patch: State | None = None) -> State:
        """Continue an interrupted thread — the human-in-the-loop path.

        `patch` is merged in first, which is how an approver injects a decision
        (`{"approval": "granted"}`) before the gated node runs.
        """
        if self.checkpointer is None:
            raise GraphError("resume requires a checkpointer")
        cp = self.checkpointer.latest(thread_id)
        if cp is None:
            raise GraphError(f"no checkpoint for thread {thread_id!r}")
        state = copy.deepcopy(cp.state)
        if patch:
            state = self._merge(state, patch)
        return self._run(state, list(cp.frontier), thread_id, step=cp.step, resuming=True)

    # -- engine ------------------------------------------------------------
    def _run(
        self,
        state: State,
        frontier: list[str],
        thread_id: str,
        step: int,
        resuming: bool = False,
    ) -> State:
        while frontier:
            if step >= self.recursion_limit:
                raise RecursionLimit(
                    f"exceeded {self.recursion_limit} supersteps on thread {thread_id!r}; "
                    f"frontier={frontier}"
                )

            # Approval gates fire before the node executes, so the checkpoint we
            # leave behind is the pre-action state — safe to inspect and patch.
            if not resuming:
                for node in frontier:
                    if node in self.interrupt_before:
                        self._checkpoint(thread_id, step, state, frontier)
                        raise Interrupt(node, thread_id, state)
            resuming = False

            started = time.perf_counter()
            results = self._execute(frontier, state)
            written: dict[str, list[str]] = {}
            for node, update in results:
                if update:
                    state = self._merge(state, update)
                    written[node] = sorted(update)
            self.trace.append(
                StepTrace(
                    step=step,
                    nodes=list(frontier),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    updates=written,
                )
            )

            step += 1
            frontier = self._next_frontier(frontier, state)
            self._checkpoint(thread_id, step, state, frontier)

        return state

    def _execute(self, frontier: list[str], state: State) -> list[tuple[str, State | None]]:
        # Each node gets its own snapshot: no node can observe another node's
        # writes from the same superstep. Removes read-write races by design.
        if len(frontier) == 1:
            node = frontier[0]
            return [(node, self.g.nodes[node](copy.deepcopy(state)))]

        with ThreadPoolExecutor(max_workers=min(self.max_parallelism, len(frontier))) as pool:
            futures = {
                pool.submit(self.g.nodes[node], copy.deepcopy(state)): node for node in frontier
            }
            out = []
            for fut, node in futures.items():
                out.append((node, fut.result()))
        # Sort so the merge order is deterministic regardless of thread finish
        # order — otherwise last-write-wins keys become a coin flip.
        order = {n: i for i, n in enumerate(frontier)}
        out.sort(key=lambda pair: order[pair[0]])
        return out

    def _next_frontier(self, frontier: list[str], state: State) -> list[str]:
        nxt: list[str] = []
        for node in frontier:
            branch = self.g.branches.get(node)
            if branch is not None:
                keys = branch.router(state)
                keys = [keys] if isinstance(keys, str) else list(keys)
                for key in keys:
                    dst = branch.mapping[key] if branch.mapping else key
                    if dst != END and dst not in nxt:
                        nxt.append(dst)
                continue
            for dst in self.g.edges.get(node, []):
                if dst != END and dst not in nxt:
                    nxt.append(dst)
        return nxt

    def _merge(self, state: State, update: State) -> State:
        out = dict(state)
        for key, value in update.items():
            reducer = self.g.reducers.get(key, last_write_wins)
            out[key] = reducer(out.get(key), value)
        return out

    def _checkpoint(self, thread_id: str, step: int, state: State, frontier: list[str]) -> None:
        if self.checkpointer is not None:
            self.checkpointer.put(
                Checkpoint(thread_id, step, copy.deepcopy(state), list(frontier))
            )

    def mermaid(self) -> str:
        """Render the topology. Cheap observability — paste into any Markdown."""
        lines = ["flowchart TD", f"    {START}([start]) --> {self.g.entry}"]
        for src, dsts in self.g.edges.items():
            for dst in dsts:
                lines.append(f"    {src} --> {dst}")
        for src, branch in self.g.branches.items():
            targets = list((branch.mapping or {}).values()) + list(branch.targets)
            for dst in targets or ["?undeclared"]:
                lines.append(f"    {src} -.-> {dst}")
        return "\n".join(lines)
