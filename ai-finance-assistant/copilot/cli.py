"""Interactive terminal chat.  Run:  python -m copilot.cli"""
import logging
import sys

from .agent import MoneyCopilot
from .categorizer import LLMCategorizer
from .llm import has_credentials
from .tools import FinanceTools


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    if not has_credentials():
        sys.exit("Set ANTHROPIC_API_KEY first (or try `python -m evals.run_evals --offline`).")
    bot = MoneyCopilot(FinanceTools(categorizer=LLMCategorizer()))
    print("Money Copilot - ask about your spending or the bank. Ctrl+C to quit.\n")
    total = 0.0
    while True:
        try:
            q = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\nbye (session cost ${total:.4f})")
            return
        if not q:
            continue
        r = bot.chat(q)
        total += r.cost_usd
        print(f"\ncopilot > {r.answer}\n   [tools: {', '.join(r.tool_calls) or 'none'} | ${r.cost_usd:.4f}]\n")


if __name__ == "__main__":
    main()
