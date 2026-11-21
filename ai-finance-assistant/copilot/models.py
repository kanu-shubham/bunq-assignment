"""Typed data contracts. Every LLM output is validated against one of these."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "Groceries", "Eating out", "Transport", "Housing", "Utilities",
    "Subscriptions", "Shopping", "Health", "Travel", "Income",
    "Transfers", "Other",
]


class Transaction(BaseModel):
    id: str
    date: date
    description: str
    amount: float  # negative = money out
    currency: str = "EUR"


class CategorizedItem(BaseModel):
    id: str
    category: Category
    merchant: str = Field(description="Clean, human-readable merchant name")
    confidence: float = Field(ge=0, le=1)


class CategorizationResult(BaseModel):
    items: list[CategorizedItem]


class DocChunk(BaseModel):
    doc: str
    section: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.doc} > {self.section}"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    answer: str
    tool_calls: list[str]
    input_tokens: int
    output_tokens: int
    cost_usd: float
