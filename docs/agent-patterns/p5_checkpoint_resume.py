"""Prototype 5 — Checkpoint and Resume.

A long-running agent will be interrupted: a deploy, an OOM kill, a rate limit, a
human walking away for the weekend. The question is not whether it dies but what
happens when it comes back. This prototype makes a run durable with an
append-only journal in SQLite, and proves the property that matters: **resuming
must not repeat a side effect that already happened.**

Scenario: an order-fulfilment agent — reserve stock, charge the card, book a
courier, email the customer. The process is killed immediately after the charge.

    python3 p5_checkpoint_resume.py

What to point at in an interview:
  * Write-ahead ordering: the journal entry for a step is committed *before* the
    loop moves on, and the step is only attempted once its intent is recorded.
  * Idempotency keys make the payment provider de-duplicate a retry that we
    cannot tell apart from a first attempt. Journal + idempotency key together
    give you effectively-once behaviour on top of at-least-once delivery.
  * Resume replays the journal to rebuild state. Nothing is recomputed by the
    model, so recovery costs no tokens.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# External services. Both de-duplicate on an idempotency key, the way a real
# payment API does; the courier API deliberately does not, to show the gap.
# --------------------------------------------------------------------------

PAYMENT_LEDGER: dict[str, str] = {}   # idempotency key -> charge id
COURIER_BOOKINGS: list[str] = []      # no dedupe: every call books a van
EMAILS_SENT: list[str] = []


def reserve_stock(order_id: str, idem: str) -> str:
    return f"reservation-{order_id}"


def charge_card(order_id: str, amount: float, idem: str) -> str:
    if idem in PAYMENT_LEDGER:
        return PAYMENT_LEDGER[idem]  # provider-side dedupe: same charge returned
    charge_id = f"ch_{len(PAYMENT_LEDGER) + 1:04d}"
    PAYMENT_LEDGER[idem] = charge_id
    return charge_id


def book_courier(order_id: str, idem: str) -> str:
    COURIER_BOOKINGS.append(order_id)
    return f"courier-{len(COURIER_BOOKINGS)}"


def send_email(order_id: str, idem: str) -> str:
    EMAILS_SENT.append(order_id)
    return f"emailed customer for {order_id}"


STEPS = [
    ("reserve_stock", reserve_stock, {}),
    ("charge_card", charge_card, {"amount": 249.00}),
    ("book_courier", book_courier, {}),
    ("send_email", send_email, {}),
]


# --------------------------------------------------------------------------
# The journal
# --------------------------------------------------------------------------


@dataclass
class Journal:
    """Append-only event log. One row per (run, step, event type).

    `started` before the call, `finished` after. A run that comes back with a
    `started` and no `finished` knows exactly one step is in doubt — which is
    the whole reason to record intent separately from outcome.
    """

    db: sqlite3.Connection

    @classmethod
    def open(cls, path: Path) -> "Journal":
        db = sqlite3.connect(path)
        db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                   run_id TEXT, step TEXT, type TEXT, payload TEXT,
                   PRIMARY KEY (run_id, step, type))"""
        )
        db.commit()
        return cls(db)

    def append(self, run_id: str, step: str, type_: str, payload: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?)",
            (run_id, step, type_, json.dumps(payload)),
        )
        self.db.commit()  # durable before the caller proceeds

    def replay(self, run_id: str) -> dict[str, dict]:
        """Rebuild run state from events. This is the resume path."""
        state: dict[str, dict] = {}
        rows = self.db.execute(
            "SELECT step, type, payload FROM events WHERE run_id=?", (run_id,)
        )
        for step, type_, payload in rows:
            state.setdefault(step, {})[type_] = json.loads(payload)
        return state


# --------------------------------------------------------------------------
# The durable loop
# --------------------------------------------------------------------------


class Crash(RuntimeError):
    """Stands in for SIGKILL / OOM / a pod being rescheduled."""


def run_order(
    run_id: str,
    order_id: str,
    journal: Journal,
    crash_after: str | None = None,
    crash_during: str | None = None,
) -> str:
    state = journal.replay(run_id)
    if state:
        done = [s for s, ev in state.items() if "finished" in ev]
        print(f"  resuming {run_id}: {len(done)} step(s) already finished -> {done}")

    for name, fn, extra in STEPS:
        record = state.get(name, {})

        if "finished" in record:
            print(f"  skip    {name:<14} (journal: {record['finished']['result']})")
            continue

        # The idempotency key is derived, not random: the same logical step in
        # the same run always produces the same key, across process restarts.
        idem = f"{run_id}:{name}"

        if "started" in record:
            print(f"  RETRY   {name:<14} (in doubt after crash; key={idem})")
        journal.append(run_id, name, "started", {"idem": idem})

        result = fn(order_id=order_id, idem=idem, **extra)
        if crash_during == name:
            # The nastiest crash: the side effect happened, the journal does not
            # know it. Only the idempotency key saves us on the retry.
            raise Crash(f"process killed mid-{name}, before the result was journalled")
        journal.append(run_id, name, "finished", {"result": result})
        print(f"  run     {name:<14} -> {result}")

        if crash_after == name:
            raise Crash(f"process killed right after {name}")

    return "order fulfilled"


if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="ckpt_"))
    journal = Journal.open(tmp / "runs.db")
    run_id, order_id = "run-8817", "ord-4412"

    print("ATTEMPT 1 (crashes after the charge)")
    try:
        run_order(run_id, order_id, journal, crash_after="charge_card")
    except Crash as exc:
        print(f"  !! {exc}\n")

    print("ATTEMPT 2 (same run id — resumes from the journal)")
    print(f"  {run_order(run_id, order_id, journal)}\n")

    print("ATTEMPT 3 (an operator re-runs it by mistake)")
    print(f"  {run_order(run_id, order_id, journal)}\n")

    print("SCENARIO B: killed *between* the charge and the journal write")
    run_b = "run-9002"
    try:
        run_order(run_b, "ord-7781", journal, crash_during="charge_card")
    except Crash as exc:
        print(f"  !! {exc}")
    print(f"  {run_order(run_b, 'ord-7781', journal)}\n")

    print(f"charges made : {PAYMENT_LEDGER}")
    print(f"couriers booked: {COURIER_BOOKINGS}")
    print(f"emails sent   : {EMAILS_SENT}")
    assert len(PAYMENT_LEDGER) == 2, "one charge per order, no more"
    assert COURIER_BOOKINGS.count("ord-4412") == 1
    print("\nOK: one charge per order across five attempts, one courier per order.")
