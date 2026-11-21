"""Skill #3a: tools the agent can call.

Rules of thumb for agent tools:
  * Few, well-named tools with tight JSON schemas (`strict: True`).
  * Return compact JSON the model can reason over - not giant dumps.
  * Validate inputs yourself and return clear error messages; the model
    reads them and self-corrects.
  * Side-effecting tools (set_budget) get extra validation.
"""
import json
from collections import defaultdict

from . import config
from .categorizer import Categorizer, RuleCategorizer
from .data import load_transactions
from .models import CategorizedItem, Transaction
from .retriever import BM25Retriever


class ToolError(Exception):
    """Raised for bad tool input; surfaced to the model as is_error=True."""


TOOL_SCHEMAS = [
    {
        "name": "get_spending_summary",
        "description": "Total money in/out per category for a month. Use for questions like 'how much did I spend on X' or 'where does my money go'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "YYYY-MM. Use the latest month in the data if the user doesn't say."},
            },
            "required": ["month"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_transactions",
        "description": "List individual transactions, filtered by merchant text and/or category. Use to find specific payments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Case-insensitive text to match in the merchant/description. Empty string for no filter."},
                "category": {"type": "string", "enum": [*config.CATEGORIES, "Any"]},
                "limit": {"type": "integer", "description": "Max results (1-50)."},
            },
            "required": ["text", "category", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_help_docs",
        "description": "Search the bank's help center (cards, payments, disputes, budgets, savings, security). Use for any 'how do I / what is the policy' question. Cite the returned source.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_budget",
        "description": "Create or update the user's monthly budget for one category. Only call this when the user explicitly asks to set a budget.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": config.CATEGORIES},
                "monthly_limit_eur": {"type": "number"},
            },
            "required": ["category", "monthly_limit_eur"],
            "additionalProperties": False,
        },
    },
]
for _schema in TOOL_SCHEMAS:
    _schema["strict"] = True  # API guarantees inputs match the schema


class FinanceTools:
    def __init__(self, categorizer: Categorizer | None = None,
                 transactions: list[Transaction] | None = None,
                 retriever: BM25Retriever | None = None):
        self.transactions = list(transactions or load_transactions())
        self.categorizer = categorizer or RuleCategorizer()
        self.retriever = retriever or BM25Retriever.from_dir()
        self.budgets: dict[str, float] = {}
        self._labels: dict[str, CategorizedItem] | None = None

    # Categorize the ledger once, lazily, and reuse it for every question.
    @property
    def labels(self) -> dict[str, CategorizedItem]:
        if self._labels is None:
            self._labels = {c.id: c for c in self.categorizer.categorize(self.transactions)}
        return self._labels

    def run(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise ToolError(f"Unknown tool '{name}'")
        return json.dumps(handler(**args), default=str)

    # ------------------------------------------------------------ handlers
    def _tool_get_spending_summary(self, month: str) -> dict:
        txs = [t for t in self.transactions if t.date.strftime("%Y-%m") == month]
        if not txs:
            months = sorted({t.date.strftime("%Y-%m") for t in self.transactions})
            raise ToolError(f"No transactions for {month}. Available months: {months}")
        totals: dict[str, float] = defaultdict(float)
        for t in txs:
            totals[self.labels[t.id].category] += t.amount
        spent = {k: round(-v, 2) for k, v in totals.items() if v < 0}
        return {
            "month": month,
            "spent_by_category_eur": dict(sorted(spent.items(), key=lambda kv: -kv[1])),
            "total_in_eur": round(sum(t.amount for t in txs if t.amount > 0), 2),
            "total_out_eur": round(-sum(t.amount for t in txs if t.amount < 0), 2),
            "budgets_eur": self.budgets,
        }

    def _tool_search_transactions(self, text: str, category: str, limit: int) -> dict:
        if not 1 <= limit <= 50:
            raise ToolError("limit must be between 1 and 50")
        needle = text.lower().strip()
        hits = []
        for t in self.transactions:
            label = self.labels[t.id]
            if category != "Any" and label.category != category:
                continue
            if needle and needle not in t.description.lower() and needle not in label.merchant.lower():
                continue
            hits.append({"date": t.date, "merchant": label.merchant, "amount_eur": t.amount,
                         "category": label.category})
        return {"count": len(hits), "transactions": hits[:limit]}

    def _tool_search_help_docs(self, query: str) -> dict:
        chunks = self.retriever.search(query, k=3)
        if not chunks:
            return {"results": [], "note": "No matching help article. Say you don't know."}
        return {"results": [{"source": c.citation, "text": c.text} for c in chunks]}

    def _tool_set_budget(self, category: str, monthly_limit_eur: float) -> dict:
        if category not in config.CATEGORIES:
            raise ToolError(f"Unknown category '{category}'")
        if not 0 < monthly_limit_eur <= 100_000:
            raise ToolError("monthly_limit_eur must be between 0 and 100000")
        self.budgets[category] = round(monthly_limit_eur, 2)
        return {"ok": True, "category": category, "monthly_limit_eur": self.budgets[category]}
