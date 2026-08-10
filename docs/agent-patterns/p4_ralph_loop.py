"""Prototype 4 — the Ralph loop.

The brute-force pattern: run the *same* prompt in a *fresh* agent context, over
and over, until the world satisfies the spec.

    while ! tests_pass; do  cat PROMPT.md | agent --dangerously-skip-permissions ; done

Nothing is remembered between iterations. The agent is stateless; the workspace
is the state. Each pass reads the spec, reads the plan, reads the test output,
does one unit of work, and writes down what it did. Convergence comes from the
repository getting closer to the spec, not from a growing transcript.

    python3 p4_ralph_loop.py            # run to green
    python3 p4_ralph_loop.py --keep     # keep the workspace to inspect it

What to point at in an interview:
  * Fresh context per iteration is the *feature*: it dodges context-window
    exhaustion and stops one bad turn from poisoning the whole run.
  * The loop needs four guardrails or it burns money forever: an objective exit
    check, an iteration cap, stall detection, and small enough units of work
    that one iteration fits comfortably in the window.
  * `fix_plan.md` is the handoff between iterations. Filesystem as memory.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Workspace scaffolding — the spec, the plan, and the (already written) tests.
# --------------------------------------------------------------------------

PROMPT_MD = """\
# Goal
Implement `src/calc.py` so that `tests/test_calc.py` passes.

# Loop instructions (read on every iteration; you remember nothing)
1. Run the tests. If they pass, write DONE to status.txt and stop.
2. Read fix_plan.md. Pick THE SINGLE most important unchecked item.
3. Implement only that item. Do not refactor code that already works.
4. Tick the item in fix_plan.md and append one line to progress.log.
"""

FIX_PLAN_MD = """\
- [ ] add(a, b)
- [ ] divide(a, b) raising ZeroDivisionError on b == 0
- [ ] percent_change(old, new)
"""

TESTS = """\
import sys
sys.path.insert(0, "src")
import calc

assert calc.add(2, 3) == 5
assert calc.divide(9, 3) == 3
try:
    calc.divide(1, 0)
except ZeroDivisionError:
    pass
else:
    raise AssertionError("divide by zero must raise")
assert calc.percent_change(200, 250) == 25.0
print("all tests passed")
"""

# What the agent "writes" for each plan item. In a real Ralph loop this is the
# coding agent's output; here it is a lookup table so the demo is deterministic.
IMPLEMENTATIONS = {
    "add(a, b)": "def add(a, b):\n    return a + b\n",
    "divide(a, b) raising ZeroDivisionError on b == 0": (
        "def divide(a, b):\n"
        "    if b == 0:\n"
        '        raise ZeroDivisionError("division by zero")\n'
        "    return a / b\n"
    ),
    "percent_change(old, new)": (
        "def percent_change(old, new):\n    return (new - old) / old * 100\n"
    ),
}


def scaffold(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "PROMPT.md").write_text(PROMPT_MD)
    (root / "fix_plan.md").write_text(FIX_PLAN_MD)
    (root / "tests" / "test_calc.py").write_text(TESTS)
    (root / "src" / "calc.py").write_text("")
    (root / "progress.log").write_text("")


# --------------------------------------------------------------------------
# One iteration of the agent. Note the signature: it gets the workspace and
# nothing else. No transcript, no memory of previous iterations.
# --------------------------------------------------------------------------


@dataclass
class Iteration:
    n: int
    read: str
    did: str
    tests: str


def run_tests(root: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "tests/test_calc.py"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    output = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, (output[-1] if output else "")


def agent_pass(root: Path, n: int) -> Iteration:
    """A single stateless agent invocation."""
    plan = (root / "fix_plan.md").read_text()
    unchecked = [ln[6:] for ln in plan.splitlines() if ln.startswith("- [ ] ")]

    ok, tail = run_tests(root)
    if ok:
        (root / "status.txt").write_text("DONE")
        return Iteration(n, "tests green", "wrote status.txt=DONE", tail)
    if not unchecked:
        # Spec unmet but plan exhausted: the agent must extend the plan rather
        # than silently spin. A real agent writes a new item here.
        return Iteration(n, "no unchecked items", "nothing to do", tail)

    item = unchecked[0]
    src = root / "src" / "calc.py"
    src.write_text(src.read_text() + "\n" + IMPLEMENTATIONS[item])
    (root / "fix_plan.md").write_text(plan.replace(f"- [ ] {item}", f"- [x] {item}", 1))
    with (root / "progress.log").open("a") as fh:
        fh.write(f"iteration {n}: implemented {item}\n")

    ok, tail = run_tests(root)
    return Iteration(n, f"{len(unchecked)} items left; tests red", f"implemented {item}", tail)


# --------------------------------------------------------------------------
# The loop itself — four guardrails, as promised.
# --------------------------------------------------------------------------


def fingerprint(root: Path) -> str:
    """Hash of every tracked file: used to detect an iteration that changed
    nothing, which is the signature of a stuck loop."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def ralph(root: Path, max_iterations: int = 10) -> str:
    stalls = 0
    for n in range(1, max_iterations + 1):
        before = fingerprint(root)
        it = agent_pass(root, n)
        after = fingerprint(root)

        print(f"--- iteration {n} (fresh context) ---")
        print(f"  read : {it.read}")
        print(f"  did  : {it.did}")
        print(f"  tests: {it.tests}")

        if (root / "status.txt").exists():  # 1. objective exit check
            return f"converged after {n} iterations"
        if before == after:  # 3. stall detection
            stalls += 1
            if stalls == 2:
                return f"stalled at iteration {n}: two passes changed nothing"
        else:
            stalls = 0
    return f"gave up after {max_iterations} iterations"  # 2. iteration cap


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="do not delete the workspace")
    args = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="ralph_"))
    scaffold(root)
    print(f"workspace: {root}\n")
    try:
        print(f"\nRESULT: {ralph(root)}")
        print(f"\nprogress.log:\n{(root / 'progress.log').read_text()}", end="")
        print(f"fix_plan.md:\n{(root / 'fix_plan.md').read_text()}", end="")
    finally:
        if not args.keep:
            shutil.rmtree(root)
        else:
            print(f"\nkept: {root}")
