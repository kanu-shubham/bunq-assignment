from copilot.retriever import BM25Retriever, chunk_markdown, tokenize
from copilot import config


def test_chunks_one_per_section():
    chunks = chunk_markdown(config.HELP_DOCS_DIR / "cards.md")
    assert [c.section for c in chunks] == ["Freezing a card", "Lost or stolen card", "Card limits"]
    assert all(c.text for c in chunks)


def test_tokenize_drops_stopwords_and_plurals():
    assert tokenize("How do I freeze my Cards?") == ["freeze", "card"]


def test_search_finds_relevant_section():
    r = BM25Retriever.from_dir()
    assert r.search("dispute a payment I don't recognise")[0].citation == "payments > Disputing a card payment"


def test_search_returns_nothing_for_unrelated_query():
    assert BM25Retriever.from_dir().search("zxqv blorp") == []
