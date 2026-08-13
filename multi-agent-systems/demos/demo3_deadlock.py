"""Prototype 3 — deadlock, livelock, and the gates that hang forever.

    python demos/demo3_deadlock.py

Each section produces a real failure and then a real fix:

  A. classic circular wait between two remediation agents, detected via the
     wait-for graph
  B. prevented by global lock ordering (break circular wait)
  C. prevented by all-or-nothing acquisition (break hold-and-wait)
  D. bounded by timeout (break no-preemption) — deadlock becomes a retry
  E. semantic deadlock: no locks at all, a cycle in the task dependency graph
  F. livelock: nothing blocked, no progress
  G. approval deadlock: the one that actually pages you at 3am
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magents.coordination import (  # noqa: E402
    Deadline,
    DeadlockError,
    LivelockDetector,
    LockTimeout,
    ResourceManager,
    WaitForGraph,
    backoff,
)

RULE = "=" * 74


def header(text: str) -> None:
    print(f"\n{RULE}\n{text}\n{RULE}")


# ---------------------------------------------------------------------------
def part_a_circular_wait() -> None:
    header("A. Circular wait — two agents, two services, opposite order")

    # Ordering off, so the manager cannot prevent it — only detect it.
    rm = ResourceManager(ordered=False, default_timeout=1.0)
    outcomes: dict[str, str] = {}
    ready = threading.Barrier(2)

    def agent(name: str, first: str, second: str) -> None:
        try:
            rm.acquire(name, first)
            ready.wait(timeout=2)
            time.sleep(0.05)  # widen the window so the interleaving is reliable
            rm.acquire(name, second)
            outcomes[name] = "completed"
            rm.release(name, second)
        except DeadlockError as exc:
            outcomes[name] = f"DeadlockError -> {exc}"
        except (LockTimeout, threading.BrokenBarrierError) as exc:
            outcomes[name] = f"{type(exc).__name__} -> {exc}"
        finally:
            rm.release(name, first)

    threads = [
        threading.Thread(target=agent, args=("remediator-A", "svc:checkout", "svc:ledger")),
        threading.Thread(target=agent, args=("remediator-B", "svc:ledger", "svc:checkout")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    for name, outcome in sorted(outcomes.items()):
        print(f"  {name}: {outcome}")
    print("\n  Both Coffman conditions 2 and 4 are present: each holds one lock")
    print("  while waiting for the other. The wait-for graph catches the cycle")
    print("  the moment the second edge is added, and one agent is aborted as")
    print("  the victim rather than both hanging.")


# ---------------------------------------------------------------------------
def part_b_ordering() -> None:
    header("B. Prevention 1 — global lock ordering (breaks circular wait)")

    rm = ResourceManager(ordered=True, default_timeout=1.0)
    results: dict[str, str] = {}
    ready = threading.Barrier(2)

    def agent(name: str, resources: list[str]) -> None:
        # Sorting is the entire fix. If every agent acquires in the same total
        # order, a cycle is impossible — there is no edge from a higher-ordered
        # resource back to a lower-ordered one.
        ready.wait(timeout=3)  # start both at the same instant, then race
        try:
            with rm.hold_all(name, resources, timeout=2.0) as acquired:
                time.sleep(0.05)
                results[name] = f"completed holding {acquired}"
        except Exception as exc:  # noqa: BLE001
            results[name] = f"{type(exc).__name__}: {exc}"

    threads = [
        threading.Thread(target=agent, args=("remediator-A", ["svc:checkout", "svc:ledger"])),
        threading.Thread(target=agent, args=("remediator-B", ["svc:ledger", "svc:checkout"])),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    for name, outcome in sorted(results.items()):
        print(f"  {name}: {outcome}")
    print("\n  Both agents asked for the locks in opposite order; both acquired in")
    print("  canonical order, so they simply serialized. No cycle is possible, so")
    print("  nothing needed detecting. This is the answer to give first —")
    print("  cheapest and strongest.")

    rm2 = ResourceManager(ordered=True)
    rm2.acquire("agent", "svc:zzz")
    try:
        rm2.acquire("agent", "svc:aaa")  # out of order while holding
    except DeadlockError as exc:
        print(f"\n  Out-of-order acquisition is rejected loudly: {exc}")
        print("  Fail fast at the call site beats a hang in production.")


# ---------------------------------------------------------------------------
def part_c_all_or_nothing() -> None:
    header("C. Prevention 2 — all-or-nothing acquisition (breaks hold-and-wait)")

    rm = ResourceManager(ordered=True, default_timeout=0.2)
    rm.acquire("other-incident", "svc:ledger")  # contended

    try:
        with rm.hold_all("remediator-A", ["svc:checkout", "svc:ledger"]):
            print("  never reached")
    except LockTimeout as exc:
        print(f"  hold_all failed: {exc}")

    holders = rm.holders()
    print(f"  lock table after the failure: {holders}")
    assert holders.get("svc:checkout") is None, "partial acquisition leaked"
    print("\n  svc:checkout was acquired and then released on the way out. Without")
    print("  the rollback, the failed agent would sit on one lock forever while")
    print("  waiting to retry — hold-and-wait, and a deadlock waiting to happen.")
    rm.release("other-incident", "svc:ledger")


# ---------------------------------------------------------------------------
def part_d_timeout() -> None:
    header("D. Prevention 3 — timeout (breaks no-preemption)")

    rm = ResourceManager(ordered=True, default_timeout=0.15)
    rm.acquire("long-running", "svc:checkout")

    for attempt in range(3):
        try:
            rm.acquire("remediator-B", "svc:checkout", timeout=0.15)
            print(f"  attempt {attempt}: acquired")
            break
        except LockTimeout as exc:
            delay = backoff(attempt)
            print(f"  attempt {attempt}: {exc} -> backing off {delay * 1000:.0f}ms")
            time.sleep(delay)
    rm.release("long-running", "svc:checkout")
    print("\n  A timeout converts a permanent hang into a retryable failure. Always")
    print("  set one, even with ordering in place — ordering protects you from the")
    print("  agents you wrote, not from a stuck tool call or a crashed holder.")
    print("  Jittered backoff matters: unjittered retries collide in lockstep and")
    print("  you have replaced a deadlock with a livelock.")


# ---------------------------------------------------------------------------
def part_e_semantic_deadlock() -> None:
    header("E. Semantic deadlock — no locks involved at all")

    # Nothing here holds a mutex. Agent A is waiting for a result only B can
    # produce, and vice versa. Structurally identical, same detection.
    graph = WaitForGraph()
    dependencies = [
        ("planner", "risk_assessor"),      # planner waits for a risk score
        ("risk_assessor", "cost_analyst"), # risk waits for a cost estimate
        ("cost_analyst", "planner"),       # cost waits for the plan. Cycle.
    ]
    for waiter, holder in dependencies:
        cycle = graph.wait(waiter, holder)
        arrow = f"  {waiter} -> waits on -> {holder}"
        if cycle:
            print(f"{arrow}   *** CYCLE: {' -> '.join(cycle + [cycle[0]])}")
        else:
            print(arrow)

    print("\n  Three agents, zero locks, and the system is wedged. The fix is not a")
    print("  lock manager — it is to make the dependency graph explicit and")
    print("  topologically sort it *before* dispatch. If it does not sort, the")
    print("  decomposition itself is wrong and no amount of retrying helps.")


# ---------------------------------------------------------------------------
def part_f_livelock() -> None:
    header("F. Livelock — busy, unblocked, not progressing")

    detector = LivelockDetector(window=6, repeats=2)
    replicas = 6
    print("  Two agents disagree: the cost agent scales down, the latency agent")
    print("  scales up. Neither is blocked. Nothing is deadlocked.\n")

    for tick in range(1, 9):
        replicas = 3 if replicas == 6 else 6
        signature = ("checkout-api", replicas)
        suspected = detector.observe(signature)
        actor = "cost_agent" if replicas == 3 else "latency_agent"
        flag = "  <-- LIVELOCK SUSPECTED" if suspected else ""
        print(f"  tick {tick}: {actor:14} sets replicas={replicas}{flag}")
        if suspected:
            break

    print("\n  A wait-for graph sees nothing here — no agent is waiting on another.")
    print("  What is observable is that the state keeps returning to values it has")
    print("  already visited. Fixes: a single owner per resource, a monotone")
    print("  progress measure, bounded iterations, and jittered backoff.")


# ---------------------------------------------------------------------------
def part_g_approval_deadlock() -> None:
    header("G. Approval deadlock — the one that actually happens")

    print("  The agent pauses for human approval. The approver is asleep.\n")

    gate = Deadline(seconds=0.3, on_expiry="escalate")
    waited = 0.0
    while not gate.expired:
        time.sleep(0.1)
        waited += 0.1
        print(f"    waiting for approval... {gate.remaining:.1f}s remaining")
    print(f"\n  deadline expired after {waited:.1f}s -> action: {gate.on_expiry}")

    print("""
  Three possible defaults, and picking the wrong one is a real outage:

    abort     safest, and correct for anything destructive. The incident stays
              broken but the automation did not make it worse.
    escalate  page a second tier, or open a war room. Usually the right default
              for a remediation system: something must happen.
    proceed   ONLY for read-only or trivially reversible actions. Defaulting a
              production write to "proceed" because nobody answered is how you
              get a 3am rollback nobody authorized.

  The one thing you must not do is wait forever. An unbounded gate is not a
  safety feature — it is a hang with good intentions, and it happens exactly
  when everybody has stopped watching.""")


def main() -> None:
    part_a_circular_wait()
    part_b_ordering()
    part_c_all_or_nothing()
    part_d_timeout()
    part_e_semantic_deadlock()
    part_f_livelock()
    part_g_approval_deadlock()
    print(f"\n{RULE}\ndone\n{RULE}")


if __name__ == "__main__":
    main()
