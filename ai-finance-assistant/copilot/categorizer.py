"""Skill #1: structured extraction.

Turns messy bank descriptions ("UBER *EATS PENDING") into validated, typed data
({"category": "Eating out", "merchant": "Uber Eats"}).

Two implementations behind one interface:
  * RuleCategorizer - keyword baseline. Free, instant, works offline.
  * LLMCategorizer  - Claude with structured outputs. Handles the long tail.
Always keep a baseline: it's your fallback AND the number the LLM must beat.
"""
import json
import logging
from typing import Protocol

from . import config
from .llm import Usage, common_params, get_client
from .models import CategorizedItem, CategorizationResult, Transaction

log = logging.getLogger(__name__)


class Categorizer(Protocol):
    def categorize(self, txs: list[Transaction]) -> list[CategorizedItem]: ...


# ---------------------------------------------------------------- baseline
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Income", ("SALARY", "SALARIS")),
    ("Transfers", ("TRANSFER", "TIKKIE", "SAVINGS")),
    ("Groceries", ("ALBERT HEIJN", "JUMBO", "LIDL", "ALDI", "PLUS ")),
    ("Eating out", ("UBER *EATS", "THUISBEZORGD", "RESTAURANT", "CAFE")),
    ("Transport", ("NS GROEP", "GVB", "SHELL", "UBER", "OV")),
    ("Housing", ("HUUR", "RENT", "HYPOTHEEK")),
    ("Utilities", ("VATTENFALL", "ENECO", "ZIGGO", "KPN", "ENERGIE")),
    ("Subscriptions", ("SPOTIFY", "NETFLIX", "BASIC-FIT", "DISNEY")),
    ("Shopping", ("BOL.COM", "ZALANDO", "AMAZON", "COOLBLUE")),
    ("Health", ("ETOS", "KRUIDVAT", "ZORGVERZ", "APOTHEEK")),
    ("Travel", ("KLM", "TRANSAVIA", "BOOKING.COM", "AIRBNB")),
]


class RuleCategorizer:
    def categorize(self, txs: list[Transaction]) -> list[CategorizedItem]:
        return [self._one(t) for t in txs]

    @staticmethod
    def _one(t: Transaction) -> CategorizedItem:
        desc = t.description.upper()
        for category, keywords in RULES:
            if any(k in desc for k in keywords):
                return CategorizedItem(id=t.id, category=category,
                                       merchant=t.description.title(), confidence=0.6)
        return CategorizedItem(id=t.id, category="Other",
                               merchant=t.description.title(), confidence=0.1)


# ---------------------------------------------------------------- LLM
SYSTEM_PROMPT = f"""You categorize bank transactions for a Dutch retail bank.

For each transaction return:
- category: exactly one of {", ".join(config.CATEGORIES)}
- merchant: the clean brand name a customer would recognise (e.g. "Albert Heijn", "Uber Eats")
- confidence: 0-1, how sure you are

Guidance:
- Positive amounts from an employer are Income; person-to-person payments (Tikkie) and own-account moves are Transfers.
- Food delivery and restaurants are Eating out; supermarkets are Groceries.
- Fuel and public transport are Transport; flights and hotels are Travel.
- Return one item per input transaction, using the same id."""


class LLMCategorizer:
    def __init__(self, client=None, batch_size: int = 25):
        self.client = client or get_client()
        self.batch_size = batch_size
        self.usage = Usage()
        self._fallback = RuleCategorizer()

    def categorize(self, txs: list[Transaction]) -> list[CategorizedItem]:
        out: list[CategorizedItem] = []
        for i in range(0, len(txs), self.batch_size):
            out.extend(self._batch(txs[i : i + self.batch_size]))
        return out

    def _batch(self, txs: list[Transaction]) -> list[CategorizedItem]:
        payload = [{"id": t.id, "description": t.description, "amount": t.amount} for t in txs]
        resp = self.client.beta.messages.parse(
            **common_params(config.CATEGORIZER_EFFORT),
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            output_format=CategorizationResult,
        )
        self.usage.add(resp.usage)

        if resp.stop_reason in ("refusal", "max_tokens") or resp.parsed_output is None:
            log.warning("LLM categorization unusable (%s); using rules", resp.stop_reason)
            return self._fallback.categorize(txs)

        # Never trust the model to return exactly the ids you sent.
        by_id = {item.id: item for item in resp.parsed_output.items}
        result = []
        for t in txs:
            item = by_id.get(t.id)
            if item is None:
                log.warning("LLM skipped %s; using rules", t.id)
                item = self._fallback._one(t)
            result.append(item)
        return result
