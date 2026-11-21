from copilot.agent import MoneyCopilot
from copilot.tools import FinanceTools
from conftest import message, text_block, tool_block


def test_tool_loop_runs_tool_and_returns_answer(fake_client):
    client = fake_client([
        message([tool_block("get_spending_summary", {"month": "2026-10"})], stop_reason="tool_use"),
        message([text_block("You spent EUR 183.85 on groceries.")]),
    ])
    bot = MoneyCopilot(FinanceTools(), client=client)
    r = bot.chat("groceries?")

    assert r.answer == "You spent EUR 183.85 on groceries."
    assert r.tool_calls == ["get_spending_summary"]
    assert r.input_tokens == 200 and r.cost_usd > 0
    # The 2nd request must carry the tool result, linked by tool_use_id.
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "tu_1" and "183.85" in result["content"]
    assert result["is_error"] is False


def test_tool_error_is_sent_back_not_raised(fake_client):
    client = fake_client([
        message([tool_block("get_spending_summary", {"month": "1999-01"})], stop_reason="tool_use"),
        message([text_block("No data for that month.")]),
    ])
    r = MoneyCopilot(FinanceTools(), client=client).chat("1999?")
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "Available months" in result["content"]
    assert r.answer == "No data for that month."


def test_parallel_tool_calls_return_in_one_message(fake_client):
    client = fake_client([
        message([tool_block("search_help_docs", {"query": "freeze"}, id="a"),
                 tool_block("search_help_docs", {"query": "limits"}, id="b")], stop_reason="tool_use"),
        message([text_block("done")]),
    ])
    MoneyCopilot(FinanceTools(), client=client).chat("q")
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_step_limit_stops_runaway_loops(fake_client):
    loop = [message([tool_block("search_help_docs", {"query": "x"})], stop_reason="tool_use")] * 3
    r = MoneyCopilot(FinanceTools(), client=fake_client(loop), max_steps=3).chat("q")
    assert "step limit" in r.answer


def test_refusal_is_handled(fake_client):
    r = MoneyCopilot(FinanceTools(), client=fake_client([message([], stop_reason="refusal")])).chat("q")
    assert "can't help" in r.answer


def test_history_persists_across_turns(fake_client):
    client = fake_client([message([text_block("hi")]), message([text_block("again")])])
    bot = MoneyCopilot(FinanceTools(), client=client)
    bot.chat("one"); bot.chat("two")
    assert len(client.requests[1]["messages"]) == 3  # user, assistant, user
