"""Skill #5: evals - how you KNOW a change helped.

    python -m evals.run_evals --offline   # free: rule baseline + retrieval
    python -m evals.run_evals             # + LLM categorizer + agent (uses API credits)

Three suites:
  1. Categorization accuracy (baseline vs LLM, split into easy and hard sets)
  2. Retrieval quality: hit@1, recall@3, MRR
  3. Agent end-to-end: right tools called + facts in the answer / state changed
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from copilot.categorizer import LLMCategorizer, RuleCategorizer
from copilot.data import load_transactions
from copilot.llm import has_credentials
from copilot.models import Transaction
from copilot.retriever import BM25Retriever

HERE = Path(__file__).parent


def load(name):
    return json.loads((HERE / name).read_text())


def eval_categorizer(categorizer, name: str) -> dict:
    gold = load("golden_categories.json")
    easy = list(load_transactions())
    hard = [Transaction(**{k: v for k, v in c.items() if k != "label"}) for c in gold["hard_cases"]]
    labels = gold["labels"] | {c["id"]: c["label"] for c in gold["hard_cases"]}

    preds = {p.id: p.category for p in categorizer.categorize(easy + hard)}
    def acc(txs):
        return sum(preds[t.id] == labels[t.id] for t in txs) / len(txs)
    errors = [f"{t.id} {t.description!r}: got {preds[t.id]}, want {labels[t.id]}"
              for t in easy + hard if preds[t.id] != labels[t.id]]
    print(f"\n== Categorizer: {name}")
    print(f"   easy accuracy {acc(easy):.0%}  |  hard accuracy {acc(hard):.0%}  |  overall {acc(easy + hard):.0%}")
    for e in errors[:8]:
        print(f"   x {e}")
    return {"easy": acc(easy), "hard": acc(hard)}


def eval_retrieval() -> dict:
    r = BM25Retriever.from_dir()
    cases = load("golden_retrieval.json")
    hit1 = recall3 = mrr = 0.0
    for c in cases:
        got = [ch.citation for ch in r.search(c["q"], k=3)]
        if got and got[0] == c["expected"]:
            hit1 += 1
        if c["expected"] in got:
            recall3 += 1
            mrr += 1 / (got.index(c["expected"]) + 1)
        else:
            print(f"   x {c['q']!r} -> {got}")
    n = len(cases)
    print(f"\n== Retrieval (BM25, {n} queries)")
    print(f"   hit@1 {hit1/n:.0%}  |  recall@3 {recall3/n:.0%}  |  MRR {mrr/n:.2f}")
    return {"hit@1": hit1 / n, "recall@3": recall3 / n}


def eval_agent() -> dict:
    from copilot.agent import MoneyCopilot
    from copilot.tools import FinanceTools

    cases = load("golden_agent.json")
    passed, cost = 0, 0.0
    print(f"\n== Agent end-to-end ({len(cases)} scenarios)")
    for c in cases:
        tools = FinanceTools(categorizer=RuleCategorizer())  # isolate agent from categorizer noise
        bot = MoneyCopilot(tools)
        r = bot.chat(c["q"])
        cost += r.cost_usd
        problems = []
        missing = set(c.get("expect_tools", [])) - set(r.tool_calls)
        if missing:
            problems.append(f"missing tools {sorted(missing)}")
        for s in c.get("must_contain", []):
            if s not in r.answer.replace(",", ""):
                problems.append(f"answer lacks {s!r}")
        if c.get("must_not_match") and re.search(c["must_not_match"], r.answer):
            problems.append("answer contains a made-up figure")
        if "expect_budgets" in c and tools.budgets != c["expect_budgets"]:
            problems.append(f"budgets {tools.budgets} != {c['expect_budgets']}")
        ok = not problems
        passed += ok
        print(f"   {'PASS' if ok else 'FAIL'}  {c['q']}  tools={dict(Counter(r.tool_calls))}  ${r.cost_usd:.4f}")
        if not ok:
            print(f"         {'; '.join(problems)}\n         answer: {r.answer[:200]!r}")
    print(f"   {passed}/{len(cases)} passed, total cost ${cost:.4f}")
    return {"pass_rate": passed / len(cases)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip suites that call the API")
    args = ap.parse_args()

    results = {"rules": eval_categorizer(RuleCategorizer(), "keyword baseline"),
               "retrieval": eval_retrieval()}
    if args.offline:
        print("\n(offline mode: skipped LLM categorizer and agent suites)")
    elif not has_credentials():
        sys.exit("\nSet ANTHROPIC_API_KEY to run the LLM suites, or pass --offline.")
    else:
        llm = LLMCategorizer()
        results["llm"] = eval_categorizer(llm, f"Claude (cost ${llm.usage.cost_usd:.4f})")
        results["agent"] = eval_agent()
    (HERE / "last_results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
