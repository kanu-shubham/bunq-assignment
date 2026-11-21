from datetime import date

from copilot.categorizer import LLMCategorizer, RuleCategorizer
from copilot.models import CategorizationResult, CategorizedItem, Transaction
from conftest import message

TXS = [
    Transaction(id="a", date=date(2026, 10, 1), description="ALBERT HEIJN 12", amount=-10),
    Transaction(id="b", date=date(2026, 10, 1), description="PICNIC BV", amount=-20),
]


def test_rules_baseline():
    out = RuleCategorizer().categorize(TXS)
    assert [o.category for o in out] == ["Groceries", "Other"]


def test_llm_uses_structured_output(fake_client):
    parsed = CategorizationResult(items=[
        CategorizedItem(id="a", category="Groceries", merchant="Albert Heijn", confidence=0.99),
        CategorizedItem(id="b", category="Groceries", merchant="Picnic", confidence=0.9),
    ])
    client = fake_client([message([], parsed=parsed)])
    out = LLMCategorizer(client=client).categorize(TXS)
    assert [o.merchant for o in out] == ["Albert Heijn", "Picnic"]
    assert client.requests[0]["output_format"] is CategorizationResult


def test_llm_missing_ids_fall_back_to_rules(fake_client):
    parsed = CategorizationResult(items=[
        CategorizedItem(id="b", category="Groceries", merchant="Picnic", confidence=0.9),
    ])
    out = LLMCategorizer(client=fake_client([message([], parsed=parsed)])).categorize(TXS)
    assert [o.id for o in out] == ["a", "b"]           # order preserved
    assert out[0].confidence == 0.6                    # came from rules


def test_llm_refusal_falls_back_to_rules(fake_client):
    client = fake_client([message([], stop_reason="refusal")])
    out = LLMCategorizer(client=client).categorize(TXS)
    assert [o.category for o in out] == ["Groceries", "Other"]


def test_batching(fake_client):
    parsed = lambda ids: CategorizationResult(items=[
        CategorizedItem(id=i, category="Other", merchant="x", confidence=0.5) for i in ids])
    client = fake_client([message([], parsed=parsed(["a"])), message([], parsed=parsed(["b"]))])
    cat = LLMCategorizer(client=client, batch_size=1)
    cat.categorize(TXS)
    assert len(client.requests) == 2
    assert cat.usage.input_tokens == 200
