from __future__ import annotations

from conftest import make_document

from ragx.config import ChunkingConfig
from ragx.ingest.chunker import chunk_document, parse_blocks

MARKDOWN = """# Handbook

Intro paragraph that sets the scene for the whole document.

## Expenses

Claims must be submitted within 30 days of the expense date.

| City | Per diem |
| --- | --- |
| Amsterdam | 65 |
| Berlin | 60 |

## Deployment

Run the deploy like this:

```bash
acme deploy --env prod --confirm
acme deploy status --watch
```

Do not deploy on a Friday.
"""


def test_heading_path_is_carried_on_every_block():
    blocks = parse_blocks(MARKDOWN)
    paths = {b.heading_path for b in blocks}
    assert ("Handbook", "Expenses") in paths
    assert ("Handbook", "Deployment") in paths


def test_tables_and_code_are_atomic():
    chunks = chunk_document(make_document(MARKDOWN), ChunkingConfig())
    table_chunks = [c for c in chunks if "| Amsterdam | 65 |" in c.text]
    assert len(table_chunks) == 1
    # The whole table travels together — header row included.
    assert "| City | Per diem |" in table_chunks[0].text
    assert "| Berlin | 60 |" in table_chunks[0].text

    code_chunks = [c for c in chunks if "acme deploy --env prod" in c.text]
    assert len(code_chunks) == 1
    assert "acme deploy status --watch" in code_chunks[0].text


def test_chunks_never_span_two_sections():
    chunks = chunk_document(make_document(MARKDOWN), ChunkingConfig())
    for chunk in chunks:
        if "30 days" in chunk.text:
            assert "Do not deploy on a Friday" not in chunk.text


def test_embed_text_is_contextualised_with_the_heading_path():
    chunks = chunk_document(make_document(MARKDOWN), ChunkingConfig())
    expense = next(c for c in chunks if "30 days" in c.text)
    assert expense.embed_text.startswith("Test Doc › Handbook › Expenses")
    # ...but the text shown to the model is the raw content.
    assert not expense.text.startswith("Test Doc")


def test_chunk_ids_are_stable_across_reruns():
    doc = make_document(MARKDOWN)
    first = chunk_document(doc, ChunkingConfig())
    second = chunk_document(doc, ChunkingConfig())
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]


def test_editing_one_section_only_changes_that_chunk_id():
    before = chunk_document(make_document(MARKDOWN), ChunkingConfig())
    edited = MARKDOWN.replace("Do not deploy on a Friday.", "Do not deploy on a Friday or Saturday.")
    after = chunk_document(make_document(edited), ChunkingConfig())

    unchanged_before = {c.chunk_id for c in before if "30 days" in c.text}
    unchanged_after = {c.chunk_id for c in after if "30 days" in c.text}
    assert unchanged_before == unchanged_after

    changed = {c.chunk_id for c in after} - {c.chunk_id for c in before}
    assert len(changed) == 1


def test_oversized_prose_is_split_under_the_ceiling():
    long_text = "# Big\n\n" + " ".join(f"Sentence number {i} about policy." for i in range(400))
    config = ChunkingConfig(target_tokens=128, max_tokens=200, overlap_tokens=0)
    chunks = chunk_document(make_document(long_text), config)
    assert len(chunks) > 3
    assert all(c.token_count <= config.max_tokens * 1.2 for c in chunks)


def test_overlap_stays_within_a_section():
    text = (
        "# Doc\n\n## A\n\n"
        + " ".join(f"Alpha sentence {i}." for i in range(60))
        + "\n\n## B\n\nBravo content lives here and is unrelated.\n"
    )
    config = ChunkingConfig(target_tokens=64, overlap_tokens=24, max_tokens=200)
    chunks = chunk_document(make_document(text), config)
    b_chunks = [c for c in chunks if c.heading_path == ("Doc", "B")]
    assert b_chunks
    assert all("Alpha sentence" not in c.text for c in b_chunks)


def test_tiny_trailing_chunks_are_merged():
    text = "# Doc\n\n## A\n\n" + "Body sentence with enough words to matter. " * 20 + "\n\nOk.\n"
    chunks = chunk_document(make_document(text), ChunkingConfig(min_tokens=48))
    assert all(c.token_count >= 20 for c in chunks)
