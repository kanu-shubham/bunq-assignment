"""Central configuration. Everything tunable lives here, not scattered in code."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TRANSACTIONS_CSV = DATA_DIR / "transactions.csv"
HELP_DOCS_DIR = DATA_DIR / "help_docs"

MODEL = os.getenv("COPILOT_MODEL", "claude-opus-5-5")
# Low effort is enough for classification; the agent gets more room to reason.
CATEGORIZER_EFFORT = os.getenv("COPILOT_CATEGORIZER_EFFORT", "low")
AGENT_EFFORT = os.getenv("COPILOT_AGENT_EFFORT", "medium")
AGENT_MAX_STEPS = int(os.getenv("COPILOT_AGENT_MAX_STEPS", "8"))

# Server-side refusal fallback (routes a declined request to another model).
FALLBACK_BETA = "server-side-fallback-2026-07-01"

CATEGORIES = [
    "Groceries", "Eating out", "Transport", "Housing", "Utilities",
    "Subscriptions", "Shopping", "Health", "Travel", "Income",
    "Transfers", "Other",
]
