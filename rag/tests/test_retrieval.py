from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import make_document

from ragx.config import ChunkingConfig, RetrievalConfig
from ragx.index.bm25 import BM25Index, tokenize
from ragx.index.hybrid import cap_per_document, fuse, mmr, reciprocal_rank_fusion
from ragx.index.vector_store import AccessFilter, InMemoryVectorStore
from ragx.ingest.chunker import chunk_document
from ragx.types import Chunk, ScoredChunk, Visibility


def _chunk(chunk_id: str, doc_id: str = "d1", text: str = "text", **kwargs) -> Chunk:
    defaults = dict(
        chunk_id=chunk_id,
        doc_id=doc_id,
        tenant_id="acme",
        text=text,
        embed_text=text,
        title="T",
        uri="u",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    defaults.update(kwargs)
    return Chunk(**defaults)


def _scored(chunk_id: str, doc_id: str, score: float) -> ScoredChunk:
    return ScoredChunk(chunk=_chunk(chunk_id, doc_id), score=score)


# ---- lexical ---------------------------------------------------------------


def test_tokenizer_keeps_identifiers_and_their_parts():
    tokens = tokenize("ERR_ACCT_4032 locked")
    assert "err_acct_4032" in tokens
    assert "acct" in tokens and "4032" in tokens


def test_bm25_finds_an_exact_error_code(system, employee):
    access = AccessFilter.for_principal(employee)
    hits = system.bm25.search("ERR_ACCT_4032", 5, access)
    assert hits
    top_chunk = system.bm25.get(hits[0][0])
    assert "ERR_ACCT_4032" in top_chunk.text


def test_bm25_delete_removes_postings():
    index = BM25Index()
    index.upsert([_chunk("c1", text="unique_token_here appears once")])
    access = AccessFilter(tenant_id="acme")
    assert index.search("unique_token_here", 5, access)
    index.delete(["c1"])
    assert index.search("unique_token_here", 5, access) == []


def test_bm25_reindex_of_same_id_does_not_duplicate_postings():
    index = BM25Index()
    index.upsert([_chunk("c1", text="alpha beta")])
    index.upsert([_chunk("c1", text="gamma delta")])
    access = AccessFilter(tenant_id="acme")
    assert index.search("alpha", 5, access) == []
    assert index.search("gamma", 5, access)
    assert len(index) == 1


# ---- access control --------------------------------------------------------


def test_tenant_isolation():
    store = InMemoryVectorStore()
    store.upsert([_chunk("a", tenant_id="acme"), _chunk("b", tenant_id="other")], [[1.0], [1.0]])
    hits = store.search([1.0], 10, AccessFilter(tenant_id="acme"))
    assert [h[0] for h in hits] == ["a"]


def test_visibility_ceiling_is_enforced():
    store = InMemoryVectorStore()
    store.upsert(
        [
            _chunk("public", visibility=Visibility.PUBLIC),
            _chunk("secret", visibility=Visibility.CONFIDENTIAL),
        ],
        [[1.0], [1.0]],
    )
    hits = store.search([1.0], 10, AccessFilter(tenant_id="acme", max_visibility=Visibility.INTERNAL))
    assert [h[0] for h in hits] == ["public"]


def test_group_membership_is_required_when_a_chunk_declares_groups():
    store = InMemoryVectorStore()
    store.upsert([_chunk("hr", acl_groups=frozenset({"people-ops"}))], [[1.0]])
    denied = store.search(
        [1.0], 10, AccessFilter(tenant_id="acme", max_visibility=Visibility.CONFIDENTIAL)
    )
    allowed = store.search(
        [1.0],
        10,
        AccessFilter(
            tenant_id="acme",
            groups=frozenset({"people-ops"}),
            max_visibility=Visibility.CONFIDENTIAL,
        ),
    )
    assert denied == []
    assert [h[0] for h in allowed] == ["hr"]


def test_confidential_corpus_document_is_invisible_to_a_normal_employee(system, employee, finance):
    access_employee = AccessFilter.for_principal(employee)
    access_finance = AccessFilter.for_principal(finance)
    employee_hits = system.bm25.search("staff engineer salary band", 10, access_employee)
    finance_hits = system.bm25.search("staff engineer salary band", 10, access_finance)

    assert all(
        system.bm25.get(cid).doc_id != "compensation-bands" for cid, _ in employee_hits
    )
    assert any(system.bm25.get(cid).doc_id == "compensation-bands" for cid, _ in finance_hits)


# ---- fusion ----------------------------------------------------------------


def test_rrf_rewards_agreement_between_channels():
    fused = reciprocal_rank_fusion(
        {
            "dense": [("a", 0.9), ("b", 0.8), ("c", 0.7)],
            "lexical": [("c", 12.0), ("a", 9.0)],
        },
        k=60,
    )
    ranking = sorted(fused, key=lambda cid: fused[cid][0], reverse=True)
    # 'a' is top-ranked in one channel and second in the other; 'b' appears once.
    assert ranking[0] == "a"
    assert ranking.index("c") < ranking.index("b")


def test_rrf_is_scale_invariant():
    small = reciprocal_rank_fusion({"dense": [("a", 0.9)], "lexical": [("b", 0.4)]})
    huge = reciprocal_rank_fusion({"dense": [("a", 0.9)], "lexical": [("b", 4000.0)]})
    assert small["a"][0] == huge["a"][0]
    assert small["b"][0] == huge["b"][0]


def test_fuse_drops_ids_that_no_longer_resolve():
    config = RetrievalConfig()
    result = fuse({"dense": [("gone", 1.0)]}, config, resolve=lambda _cid: None)
    assert result == []


def test_per_document_cap():
    scored = [_scored(f"c{i}", "doc-a", 1.0 - i / 10) for i in range(5)]
    scored.append(_scored("c9", "doc-b", 0.1))
    capped = cap_per_document(scored, cap=2)
    assert [s.chunk.chunk_id for s in capped] == ["c0", "c1", "c9"]


def test_mmr_prefers_a_different_document_over_a_near_duplicate():
    scored = [
        _scored("a1", "doc-a", 1.0),
        _scored("a2", "doc-a", 0.99),
        _scored("b1", "doc-b", 0.90),
    ]
    vectors = {"a1": [1.0, 0.0], "a2": [1.0, 0.0], "b1": [0.0, 1.0]}
    picked = mmr(scored, vectors, k=2, lambda_=0.5)
    assert [s.chunk.chunk_id for s in picked] == ["a1", "b1"]


# ---- end to end retrieval --------------------------------------------------


@pytest.mark.parametrize(
    "question,expected_doc",
    [
        ("How long do I have to submit an expense claim?", "expense-policy"),
        ("ERR_ACCT_4032", "security-incident-response"),
        ("who do I page for a sev-1 at night", "oncall-rotation"),
        ("home office allowance", "remote-work-policy"),
    ],
)
def test_hybrid_retrieval_finds_the_right_document(system, employee, question, expected_doc):
    access = AccessFilter.for_principal(employee)
    result = system.retriever.retrieve(question, access)
    doc_ids = [c.chunk.doc_id for c in result.candidates]
    assert expected_doc in doc_ids[:5]


def test_retrieval_provenance_is_kept(system, employee):
    access = AccessFilter.for_principal(employee)
    result = system.retriever.retrieve("expense claim deadline", access)
    top = result.candidates[0]
    assert set(top.component_ranks) & {"dense", "lexical"}


def test_ingest_is_incremental(system, corpus_dir):
    from ragx.ingest.loaders import load_directory

    before = len(system.vector_store)
    stats = system.ingest.ingest(load_directory(corpus_dir, tenant_id="acme"))
    assert stats.chunks_created == 0  # nothing changed, so nothing was embedded
    assert stats.chunks_unchanged > 0
    assert len(system.vector_store) == before


def test_ingest_replaces_only_edited_chunks(system):
    doc = make_document("# A\n\nOriginal body sentence about vacation policy.\n", doc_id="edit-me")
    system.ingest.ingest([doc])
    original_ids = set(system.vector_store.doc_chunk_ids("edit-me"))

    edited = make_document("# A\n\nRewritten body sentence about vacation policy.\n", doc_id="edit-me")
    stats = system.ingest.ingest([edited])
    new_ids = set(system.vector_store.doc_chunk_ids("edit-me"))

    assert stats.chunks_created == 1
    assert stats.chunks_deleted == 1
    assert new_ids != original_ids
    assert len(new_ids) == 1


def test_document_deletion_purges_both_indices(system):
    doc = make_document("# Temp\n\nSomething temporary about widget calibration.\n", doc_id="temp")
    system.ingest.ingest([doc])
    access = AccessFilter(tenant_id="acme")
    assert system.bm25.search("widget calibration", 5, access)

    system.ingest.delete_documents(["temp"])
    assert system.bm25.search("widget calibration", 5, access) == []
    assert system.vector_store.doc_chunk_ids("temp") == []


def test_chunking_config_change_is_visible_in_chunk_ids():
    doc = make_document("# A\n\n" + "Sentence about policy. " * 80)
    small = chunk_document(doc, ChunkingConfig(target_tokens=64, max_tokens=128))
    large = chunk_document(doc, ChunkingConfig(target_tokens=512, max_tokens=900))
    assert {c.chunk_id for c in small} != {c.chunk_id for c in large}
