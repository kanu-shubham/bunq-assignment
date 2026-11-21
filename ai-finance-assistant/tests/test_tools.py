import json

import pytest

from copilot.tools import TOOL_SCHEMAS, FinanceTools, ToolError


@pytest.fixture
def tools():
    return FinanceTools()


def test_spending_summary(tools):
    out = json.loads(tools.run("get_spending_summary", {"month": "2026-10"}))
    assert out["spent_by_category_eur"]["Groceries"] == 183.85
    assert out["total_in_eur"] == 3468.5


def test_unknown_month_is_a_helpful_error(tools):
    with pytest.raises(ToolError, match="Available months"):
        tools.run("get_spending_summary", {"month": "1999-01"})


def test_search_transactions(tools):
    out = json.loads(tools.run("search_transactions", {"text": "netflix", "category": "Any", "limit": 5}))
    assert out["count"] == 1 and out["transactions"][0]["amount_eur"] == -13.99


def test_set_budget_validates(tools):
    with pytest.raises(ToolError):
        tools.run("set_budget", {"category": "Groceries", "monthly_limit_eur": -5})
    tools.run("set_budget", {"category": "Groceries", "monthly_limit_eur": 300})
    assert tools.budgets == {"Groceries": 300}


def test_help_docs_have_citations(tools):
    out = json.loads(tools.run("search_help_docs", {"query": "freeze card"}))
    assert out["results"][0]["source"] == "cards > Freezing a card"


def test_every_schema_has_a_handler(tools):
    for s in TOOL_SCHEMAS:
        assert hasattr(tools, f"_tool_{s['name']}")
        assert s["strict"] and s["input_schema"]["additionalProperties"] is False
