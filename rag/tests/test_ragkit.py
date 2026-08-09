"""Tests for ragkit.

Weighted towards the things that fail silently in a RAG system: chunk
boundaries, filter placement, cache scoping, citation verification, and the
tool-result contract. A retrieval bug does not raise — it just returns slightly
worse results forever.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import (  # noqa: E402
    ChunkConfig,
    Document,
    HashingEmbedder,
    MetadataFilter,
    PipelineConfig,
    RagPipeline,
    SemanticCache,
    chunk_document,
    load_corpus,
    load_queries,
)
from ragkit.answer import Citation, ExtractiveAnswerer, coverage, verify_citations  # noqa: E402
from ragkit.corpus import parse_frontmatter  # noqa: E402
from ragkit.evaluation import (  # noqa: E402
    ndcg_at_k,
    paired_bootstrap,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from ragkit.hybrid import dedupe_by_document, reciprocal_rank_fusion  # noqa: E402
from ragkit.parallel import ToolCall, execute_tool_calls, extract_tool_calls  # noqa: E402
from ragkit.query_rewrite import RuleBasedRewriter  # noqa: E402
from ragkit.rerank import LexicalCrossEncoder, _min_covering_span  # noqa: E402
from ragkit.textutil import char_ngrams, tokenize  # noqa: E402
from ragkit.tools import (  # noqa: E402
    ToolExecutionError,
    ToolInputError,
    ToolRegistry,
    ToolSpec,
    lint_tool,
    validate_against_schema,
)
from ragkit.types import Chunk, ScoredChunk  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "corpus"
QUERIES = ROOT / "data" / "eval" / "queries.jsonl"


# --------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def docs():
    return load_corpus(CORPUS)


@pytest.fixture(scope="module")
def pipeline(docs):
    return RagPipeline.build(docs, PipelineConfig(top_k=5))


def make_chunk(chunk_id="d#000", doc_id="d", body="text", prefix="", **meta) -> Chunk:
    return Chunk(chunk_id, doc_id, 0, body, prefix, meta)


# ------------------------------------------------------------------- textutil


def test_identifier_survives_tokenisation():
    tokens = tokenize("payments.sepa_instant.enabled failed")
    assert "payments.sepa_instant.enabled" in tokens, "compound identifier must survive"
    assert {"payments", "sepa", "instant", "enabled"} <= set(tokens), "parts must be reachable"


def test_error_code_is_not_shredded_into_common_words():
    tokens = tokenize("ERR_CURRENCY_MISMATCH")
    assert "err_currency_mismatch" in tokens


def test_stopwords_dropped_but_content_kept():
    tokens = tokenize("how do I roll back a deploy")
    assert "the" not in tokens and "a" not in tokens
    assert {"roll", "back", "deploy"} <= set(tokens)


def test_char_ngrams_are_boundary_marked():
    grams = set(char_ngrams("lag", sizes=(3,)))
    assert "#la" in grams and "ag#" in grams
    # Without markers "lag" would be a substring of "flagged"; with them it is not.
    assert "#lag#" in set(char_ngrams("lag", sizes=(5,)))


# --------------------------------------------------------------------- corpus


def test_frontmatter_parsing():
    meta, body = parse_frontmatter("---\ntitle: X\ntags: [a, b]\n---\n\nbody text\n")
    assert meta == {"title": "X", "tags": ["a", "b"]}
    assert body.strip() == "body text"


def test_frontmatter_absent_is_not_an_error():
    meta, body = parse_frontmatter("# Just a heading\n")
    assert meta == {} and body.startswith("# Just")


def test_malformed_frontmatter_raises():
    with pytest.raises(ValueError):
        parse_frontmatter("---\nthis line has no colon\n---\nbody")


def test_corpus_loads_with_expected_metadata(docs):
    assert len(docs) > 50
    doc = next(d for d in docs if d.doc_id == "adr-0012-idempotency-keys")
    assert doc.metadata["doc_type"] == "adr"
    assert doc.metadata["team"] == "payments"
    assert "idempotency" in doc.metadata["tags"]


# ------------------------------------------------------------------- chunking


def test_chunker_never_splits_a_table():
    text = (
        "# Title\n\nIntro paragraph that is reasonably long so it fills space.\n\n"
        "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n\nTrailing paragraph.\n"
    )
    doc = Document("d", "T", text, "p")
    chunks = chunk_document(doc, ChunkConfig(target_words=5, max_words=12))
    tables = [c for c in chunks if "| a | b |" in c.body]
    assert len(tables) == 1, "the table must live in exactly one chunk"
    assert "| 3 | 4 |" in tables[0].body, "and must not be cut in half"


def test_chunker_never_splits_a_code_fence():
    text = "# T\n\nPara.\n\n```sh\nline one\nline two\nline three\n```\n\nAfter.\n"
    doc = Document("d", "T", text, "p")
    chunks = chunk_document(doc, ChunkConfig(target_words=4, max_words=8))
    fenced = [c for c in chunks if "line one" in c.body]
    assert len(fenced) == 1
    assert "line three" in fenced[0].body


def test_chunk_carries_heading_path(docs):
    doc = next(d for d in docs if d.doc_id == "runbook-deploy-rollback")
    chunks = chunk_document(doc)
    assert any("Manual rollback" in c.context_prefix for c in chunks)
    assert all(c.context_prefix.startswith(doc.title) for c in chunks)


def test_chunk_text_combines_prefix_and_body():
    chunk = make_chunk(body="body", prefix="Title > Heading")
    assert chunk.text == "Title > Heading\n\nbody"


def test_no_empty_chunks(pipeline):
    assert all(c.body.strip() for c in pipeline.chunks)


# ----------------------------------------------------------------- embeddings


def test_embeddings_are_unit_length():
    emb = HashingEmbedder(dim=256).fit(["alpha beta", "gamma delta"])
    vectors = emb.encode(["alpha beta gamma"])
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


def test_embedding_is_deterministic():
    a = HashingEmbedder(dim=256).fit(["x y z"])
    b = HashingEmbedder(dim=256).fit(["x y z"])
    assert np.allclose(a.encode(["hello world"]), b.encode(["hello world"]))


def test_typo_is_closer_to_its_word_than_to_an_unrelated_word():
    emb = HashingEmbedder(dim=1024).fit(
        ["reconciliation break triage", "certificate rotation scheme", "kafka consumer lag"]
    )
    vecs = emb.encode(["reconciliaton", "reconciliation", "kafka"])
    typo, correct, unrelated = vecs
    assert float(typo @ correct) > float(typo @ unrelated), (
        "character n-grams are the reason dense retrieval survives a typo"
    )


def test_unfitted_embedder_still_encodes():
    emb = HashingEmbedder(dim=128)
    assert emb.encode(["anything"]).shape == (1, 128)


def test_fit_on_empty_corpus_raises():
    with pytest.raises(ValueError):
        HashingEmbedder().fit([])


# --------------------------------------------------------------------- fusion


def test_rrf_prefers_a_document_both_lists_agree_on():
    a = make_chunk("a", "A")
    b = make_chunk("b", "B")
    c = make_chunk("c", "C")
    lex = [ScoredChunk(a, 9.0), ScoredChunk(b, 8.0)]
    den = [ScoredChunk(c, 0.9), ScoredChunk(b, 0.8)]
    fused = reciprocal_rank_fusion([lex, den], component_names=["bm25", "dense"])
    assert fused[0].chunk_id == "b", "agreed-on results should win over either list's top hit"


def test_rrf_is_immune_to_score_scale():
    a, b = make_chunk("a", "A"), make_chunk("b", "B")
    small = [ScoredChunk(a, 0.001), ScoredChunk(b, 0.0005)]
    huge = [ScoredChunk(a, 10_000.0), ScoredChunk(b, 5_000.0)]
    assert [s.chunk_id for s in reciprocal_rank_fusion([small])] == \
           [s.chunk_id for s in reciprocal_rank_fusion([huge])]


def test_dedupe_by_document_caps_contribution():
    chunks = [ScoredChunk(make_chunk(f"d#{i}", "d"), 1.0 / (i + 1)) for i in range(5)]
    assert len(dedupe_by_document(chunks, max_per_doc=2)) == 2


# -------------------------------------------------------------------- filters


def test_filter_matches_on_equality_and_tags():
    chunk = make_chunk(doc_type="runbook", team="payments", tags=["oncall", "latency"])
    assert MetadataFilter(equals={"doc_type": "runbook"}).matches(chunk)
    assert not MetadataFilter(equals={"doc_type": "adr"}).matches(chunk)
    assert MetadataFilter(has_tags=["oncall"]).matches(chunk)
    assert not MetadataFilter(has_tags=["oncall", "missing"]).matches(chunk)


def test_filter_date_bounds():
    chunk = make_chunk(updated="2026-03-15")
    assert MetadataFilter(updated_after="2026-01-01").matches(chunk)
    assert not MetadataFilter(updated_after="2026-06-01").matches(chunk)
    assert MetadataFilter(updated_before="2026-06-01").matches(chunk)


def test_empty_filter_produces_no_mask():
    assert MetadataFilter().mask([make_chunk()]) is None


def test_filter_cache_key_is_order_independent():
    a = MetadataFilter(equals={"team": "x", "doc_type": "y"})
    b = MetadataFilter(equals={"doc_type": "y", "team": "x"})
    assert a.cache_key() == b.cache_key()


def test_filter_shorthand_parsing():
    f = MetadataFilter.parse({"doc_type": "adr"})
    assert f.equals == {"doc_type": "adr"}


def test_prefilter_returns_k_results_where_postfilter_would_not(pipeline):
    """The bug this whole design exists to prevent."""
    mfilter = MetadataFilter(equals={"doc_type": "postmortem"})
    pre = pipeline.retrieve("duplicate", 5, metadata_filter=mfilter).chunks
    post = [sc for sc in pipeline.retrieve("duplicate", 5).chunks if mfilter.matches(sc.chunk)]
    assert all(sc.chunk.metadata["doc_type"] == "postmortem" for sc in pre)
    assert len(pre) > len(post), "post-filtering silently loses results"


# ------------------------------------------------------------------ retrieval


def test_bm25_finds_an_exact_error_code(pipeline):
    docs = pipeline.retrieve("ERR_CURRENCY_MISMATCH", 5).doc_ids()
    assert "readme-ledger-core" in docs


def test_hybrid_beats_a_typo(pipeline):
    docs = pipeline.retrieve("reconciliaton break triage", 5).doc_ids()
    assert "runbook-ledger-reconciliation" in docs


def test_retrieval_is_deterministic(pipeline):
    a = pipeline.retrieve("how do I roll back a deploy", 5)
    b = pipeline.retrieve("how do I roll back a deploy", 5)
    assert [s.chunk_id for s in a.chunks] == [s.chunk_id for s in b.chunks]


def test_scores_are_monotonically_decreasing(pipeline):
    scores = [s.score for s in pipeline.retrieve("incident severity", 10).chunks]
    assert scores == sorted(scores, reverse=True)


def test_retrieve_respects_k(pipeline):
    assert len(pipeline.retrieve("deploy", 3).chunks) <= 3


def test_trace_records_the_stages(pipeline):
    trace = pipeline.retrieve("kafka consumer lag", 5).trace
    assert {"rewrite_ms", "retrieve_ms", "rerank_ms", "latency_ms"} <= set(trace)


# ------------------------------------------------------------------- reranking


def test_min_covering_span_finds_the_tightest_window():
    positions = [(0, "a"), (5, "b"), (6, "a"), (7, "b")]
    assert _min_covering_span(positions, 2) == 2  # positions 6 and 7


def test_min_covering_span_none_when_unreachable():
    assert _min_covering_span([(0, "a")], 2) is None


def test_cross_encoder_prefers_adjacent_terms_over_scattered_ones():
    scorer = LexicalCrossEncoder(idf={"idempotency": 4.0, "key": 3.0, "reuse": 4.0})
    near, _ = scorer.score_pair("idempotency key reuse", "an idempotency key reuse returns 409")
    far, _ = scorer.score_pair(
        "idempotency key reuse",
        "idempotency is discussed here. " + "filler " * 40 + " a key. " + "filler " * 40 + " reuse.",
    )
    assert near > far, "proximity and phrase features must separate these"


def test_reranker_returns_at_most_k(pipeline):
    scorer = LexicalCrossEncoder.from_embedder(pipeline.embedder)
    candidates = pipeline.retrieve("incident severity levels", 10).chunks
    assert len(scorer.rerank("incident severity levels", candidates, 3)) == 3


def test_reranker_handles_empty_candidates():
    assert LexicalCrossEncoder().rerank("q", [], 5) == []


def test_prior_weight_one_preserves_first_stage_order(pipeline):
    from ragkit.rerank import CrossEncoderWeights

    scorer = LexicalCrossEncoder.from_embedder(
        pipeline.embedder, weights=CrossEncoderWeights(prior_weight=1.0)
    )
    candidates = pipeline.retrieve("deploy rollback", 8).chunks
    reranked = scorer.rerank("deploy rollback", candidates, 8)
    assert [c.chunk_id for c in reranked] == [c.chunk_id for c in candidates]


# -------------------------------------------------------------- query rewrite


def test_rewriter_strips_question_scaffolding():
    result = RuleBasedRewriter().rewrite("how do I roll back a deploy")
    assert any("roll back deploy" in v for v in result.variants)


def test_rewriter_expands_domain_vocabulary():
    result = RuleBasedRewriter().rewrite("how much holiday do I get")
    assert any("annual leave" in v for v in result.variants)


def test_rewriter_decomposes_a_conjunctive_question():
    result = RuleBasedRewriter().rewrite(
        "what is the payments rate limit and how do I roll back a deploy"
    )
    assert result.strategy_trace.get("decompose"), "two independent asks should split"


def test_rewriter_leaves_a_precise_query_alone():
    result = RuleBasedRewriter().rewrite("ERR_CURRENCY_MISMATCH")
    assert result.queries == ["ERR_CURRENCY_MISMATCH"]


def test_queries_property_deduplicates_and_keeps_original_first():
    result = RuleBasedRewriter().rewrite("deploy rollback")
    assert result.queries[0] == "deploy rollback"
    assert len(result.queries) == len(set(result.queries))


# --------------------------------------------------------------------- cache


def test_exact_hit_before_semantic():
    emb = HashingEmbedder(dim=256).fit(["a b c"])
    cache: SemanticCache[str] = SemanticCache(emb, threshold=0.9)
    cache.put("hello world", "v")
    assert cache.get("hello world").kind == "exact"
    assert cache.get("HELLO   WORLD").kind == "exact", "normalised, not byte-compared"


def test_semantic_hit_within_threshold():
    emb = HashingEmbedder(dim=1024).fit(["kafka consumer lag runbook triage"])
    cache: SemanticCache[str] = SemanticCache(emb, threshold=0.5)
    cache.put("kafka consumer lag", "v")
    assert cache.get("kafka consumer lag runbook").hit


def test_high_threshold_prevents_a_wrong_hit():
    emb = HashingEmbedder(dim=1024).fit(["rolling back a deploy", "rolling back a migration"])
    cache: SemanticCache[str] = SemanticCache(emb, threshold=0.99)
    cache.put("how do I roll back a deploy", "deploy answer")
    assert not cache.get("how do I roll back a migration").hit


def test_scope_isolation():
    emb = HashingEmbedder(dim=256).fit(["a b"])
    cache: SemanticCache[str] = SemanticCache(emb, threshold=0.5)
    cache.put("same question", "tenant-a", scope="tenant-a")
    assert cache.get("same question", scope="tenant-a").hit
    assert not cache.get("same question", scope="tenant-b").hit, "scope is part of the key"


def test_ttl_expires_entries():
    clock = {"t": 0.0}
    emb = HashingEmbedder(dim=256).fit(["a b"])
    cache: SemanticCache[str] = SemanticCache(
        emb, threshold=0.5, ttl_seconds=10.0, clock=lambda: clock["t"]
    )
    cache.put("q", "v")
    clock["t"] = 5.0
    assert cache.get("q").hit
    clock["t"] = 20.0
    assert not cache.get("q").hit
    assert cache.stats.expirations >= 1


def test_lru_eviction_respects_capacity():
    emb = HashingEmbedder(dim=256).fit(["a b c d"])
    cache: SemanticCache[str] = SemanticCache(emb, threshold=0.99, max_entries=2, ttl_seconds=None)
    for i in range(5):
        cache.put(f"query number {i}", str(i))
    assert len(cache) == 2
    assert cache.stats.evictions == 3


def test_put_overwrites_rather_than_duplicating():
    emb = HashingEmbedder(dim=256).fit(["a b"])
    cache: SemanticCache[str] = SemanticCache(emb, threshold=0.99, ttl_seconds=None)
    cache.put("q", "first")
    cache.put("q", "second")
    assert len(cache) == 1
    assert cache.get("q").value == "second"


def test_invalid_threshold_rejected():
    emb = HashingEmbedder(dim=64).fit(["a b"])
    with pytest.raises(ValueError):
        SemanticCache(emb, threshold=0.0)


# ---------------------------------------------------------------------- tools


def test_schema_validation_accepts_valid_input():
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}, "k": {"type": "integer", "maximum": 10}},
        "required": ["q"],
        "additionalProperties": False,
    }
    validate_against_schema({"q": "x", "k": 5}, schema)


@pytest.mark.parametrize(
    "payload",
    [
        {"k": 5},                       # missing required
        {"q": "x", "extra": 1},         # additionalProperties: false
        {"q": "x", "k": "five"},        # wrong type
        {"q": "x", "k": 99},            # above maximum
    ],
)
def test_schema_validation_rejects_bad_input(payload):
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}, "k": {"type": "integer", "maximum": 10}},
        "required": ["q"],
        "additionalProperties": False,
    }
    with pytest.raises(ToolInputError):
        validate_against_schema(payload, schema)


def test_bool_does_not_satisfy_integer():
    with pytest.raises(ToolInputError):
        validate_against_schema(True, {"type": "integer"})


def test_enum_is_enforced():
    with pytest.raises(ToolInputError):
        validate_against_schema("Runbooks", {"type": "string", "enum": ["runbook"]})


def test_lint_flags_a_thin_description():
    spec = ToolSpec(
        name="search", description="Searches.", handler=lambda q: None,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    rules = {f.rule for f in lint_tool(spec)}
    assert {"description-length", "when-to-use", "when-not-to-use"} <= rules


def test_lint_passes_the_real_registry(pipeline):
    errors = [f for f in pipeline.build_tool_registry().lint() if f.severity == "error"]
    assert not errors, errors


def test_registry_rejects_a_handler_that_cannot_accept_the_schema():
    with pytest.raises(ValueError, match="does not accept"):
        ToolRegistry([
            ToolSpec(
                name="t", description="d",
                input_schema={"type": "object", "properties": {"unknown": {"type": "string"}}},
                handler=lambda other: None,
            )
        ])


def test_registry_rejects_duplicate_names():
    spec = ToolSpec(
        name="t", description="d",
        input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        handler=lambda a: a,
    )
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry([spec, spec])


def test_side_effecting_tool_is_not_parallel_safe():
    spec = ToolSpec(
        name="w", description="d",
        input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        handler=lambda a: a, side_effecting=True, parallel_safe=True,
    )
    assert spec.parallel_safe is False


# ------------------------------------------------------------------- parallel


def _registry_with(handler, **kwargs) -> ToolRegistry:
    return ToolRegistry([
        ToolSpec(
            name="t", description="d",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}},
                          "required": ["x"], "additionalProperties": False},
            handler=handler, **kwargs,
        )
    ])


def test_extract_tool_calls_from_blocks():
    blocks = [
        {"type": "text", "text": "thinking"},
        {"type": "tool_use", "id": "a", "name": "t", "input": {"x": "1"}},
        {"type": "tool_use", "id": "b", "name": "t", "input": {"x": "2"}},
    ]
    calls = extract_tool_calls(blocks)
    assert [c.id for c in calls] == ["a", "b"]


def test_every_call_gets_a_result_even_when_it_fails():
    def handler(x):
        if x == "bad":
            raise ToolExecutionError("nope")
        return x

    registry = _registry_with(handler, max_retries=0)
    calls = [ToolCall("1", "t", {"x": "ok"}), ToolCall("2", "t", {"x": "bad"})]
    report = execute_tool_calls(registry, calls)
    assert len(report.results) == 2, "a dropped tool_result is a malformed transcript"
    assert report.results[1].is_error
    blocks = report.to_anthropic_user_message()["content"]
    assert blocks[1]["is_error"] is True
    assert {b["tool_use_id"] for b in blocks} == {"1", "2"}


def test_results_are_returned_in_call_order():
    import time as _time

    def handler(x):
        _time.sleep(0.05 if x == "slow" else 0.0)
        return x

    registry = _registry_with(handler)
    calls = [ToolCall("1", "t", {"x": "slow"}), ToolCall("2", "t", {"x": "fast"})]
    report = execute_tool_calls(registry, calls)
    assert [r.call.id for r in report.results] == ["1", "2"]


def test_all_results_land_in_one_user_message():
    registry = _registry_with(lambda x: x)
    calls = [ToolCall(str(i), "t", {"x": str(i)}) for i in range(4)]
    message = execute_tool_calls(registry, calls).to_anthropic_user_message()
    assert message["role"] == "user"
    assert len(message["content"]) == 4


def test_retryable_failure_is_retried():
    state = {"n": 0}

    def handler(x):
        state["n"] += 1
        if state["n"] < 3:
            raise ToolExecutionError("transient", retryable=True)
        return "ok"

    report = execute_tool_calls(_registry_with(handler, max_retries=3), [ToolCall("1", "t", {"x": "a"})])
    assert not report.results[0].is_error
    assert report.results[0].attempts == 3


def test_non_retryable_failure_is_not_retried():
    state = {"n": 0}

    def handler(x):
        state["n"] += 1
        raise ToolExecutionError("permanent", retryable=False)

    execute_tool_calls(_registry_with(handler, max_retries=5), [ToolCall("1", "t", {"x": "a"})])
    assert state["n"] == 1


def test_bad_arguments_are_not_retried():
    state = {"n": 0}

    def handler(x):
        state["n"] += 1
        return x

    report = execute_tool_calls(
        _registry_with(handler, max_retries=5), [ToolCall("1", "t", {"wrong": "a"})]
    )
    assert report.results[0].is_error
    assert state["n"] == 0, "a schema violation never becomes valid on retry"


def test_unknown_tool_produces_an_actionable_error():
    report = execute_tool_calls(_registry_with(lambda x: x), [ToolCall("1", "missing", {})])
    assert report.results[0].is_error
    assert "unknown tool" in str(report.results[0].content)


def test_parallel_is_faster_than_serial():
    import time as _time

    def handler(x):
        _time.sleep(0.1)
        return x

    registry = _registry_with(handler)
    calls = [ToolCall(str(i), "t", {"x": str(i)}) for i in range(6)]
    report = execute_tool_calls(registry, calls, max_workers=6)
    assert report.wall_ms < report.serial_ms * 0.6


def test_no_calls_is_not_an_error():
    report = execute_tool_calls(_registry_with(lambda x: x), [])
    assert report.results == [] and report.wall_ms == 0.0


# -------------------------------------------------------------------- answers


def test_verify_citations_accepts_a_verbatim_quote():
    chunk = ScoredChunk(make_chunk(body="Retries use exponential backoff with full jitter."), 1.0)
    out = verify_citations([Citation(1, "", "", "exponential backoff with full jitter")], [chunk])
    assert out[0].verified


def test_verify_citations_tolerates_reflowed_whitespace():
    chunk = ScoredChunk(make_chunk(body="Retries use exponential\nbackoff with   full jitter."), 1.0)
    out = verify_citations([Citation(1, "", "", "exponential backoff with full jitter")], [chunk])
    assert out[0].verified, "a quote broken across a line is not a fabrication"


def test_verify_citations_rejects_a_fabricated_quote():
    chunk = ScoredChunk(make_chunk(body="Retries use exponential backoff."), 1.0)
    out = verify_citations([Citation(1, "", "", "retries are capped at three attempts")], [chunk])
    assert not out[0].verified


def test_verify_citations_rejects_an_out_of_range_index():
    chunk = ScoredChunk(make_chunk(body="text here for the quote"), 1.0)
    out = verify_citations([Citation(7, "", "", "text here for the quote")], [chunk])
    assert not out[0].verified and "not in the provided context" in out[0].note


def test_verify_citations_rejects_a_quote_too_short_to_mean_anything():
    chunk = ScoredChunk(make_chunk(body="the retry policy is documented"), 1.0)
    out = verify_citations([Citation(1, "", "", "the retry")], [chunk])
    assert not out[0].verified


def test_coverage_counts_cited_sentences():
    citations = [Citation(1, "d", "c", "q", verified=True)]
    assert coverage("First claim [1]. Second claim.", citations) == 0.5


def test_extractive_answerer_abstains_with_no_results():
    answer = ExtractiveAnswerer().answer("anything", [])
    assert answer.abstained and answer.abstain_reason.value == "no_results"


def test_extractive_answerer_abstains_on_irrelevant_context():
    chunk = ScoredChunk(make_chunk(body="The expense policy covers train travel and hotels."), 1.0)
    answer = ExtractiveAnswerer(min_relevance=5.0).answer("kafka partition rebalancing", [chunk])
    assert answer.abstained and answer.abstain_reason.value == "low_relevance"


def test_extractive_answer_is_fully_supported_by_construction(pipeline):
    chunks = pipeline.retrieve("what is the webhook retry schedule", 5).chunks
    answer = ExtractiveAnswerer(LexicalCrossEncoder.from_embedder(pipeline.embedder)).answer(
        "what is the webhook retry schedule", chunks
    )
    if not answer.abstained:
        assert answer.fully_supported, "extracted sentences must verify against their source"
        assert answer.citations


# ----------------------------------------------------------------- evaluation


def test_metric_definitions():
    retrieved = ["a", "b", "c", "d"]
    gold = {"c", "z"}
    assert recall_at_k(retrieved, gold, 2) == 0.0
    assert recall_at_k(retrieved, gold, 3) == 0.5
    assert precision_at_k(retrieved, gold, 4) == 0.25
    assert reciprocal_rank(retrieved, gold) == pytest.approx(1 / 3)
    assert ndcg_at_k(["c"], {"c"}, 1) == 1.0


def test_reciprocal_rank_is_zero_when_nothing_is_found():
    assert reciprocal_rank(["a", "b"], {"z"}) == 0.0


def test_eval_queries_reference_documents_that_exist(docs):
    known = {d.doc_id for d in docs}
    for query in load_queries(QUERIES):
        missing = set(query.gold_doc_ids) - known
        assert not missing, f"{query.query_id} references unknown documents: {missing}"


def test_eval_splits_are_disjoint_and_complete():
    dev = {q.query_id for q in load_queries(QUERIES, split="dev")}
    test = {q.query_id for q in load_queries(QUERIES, split="test")}
    every = {q.query_id for q in load_queries(QUERIES)}
    assert not (dev & test), "a query in both splits invalidates the held-out measurement"
    assert dev | test == every


def test_paired_bootstrap_reports_no_difference_for_identical_reports(pipeline):
    from ragkit.evaluation import evaluate

    queries = load_queries(QUERIES, split="dev")
    report = evaluate(pipeline, queries)
    result = paired_bootstrap(report, report, "recall@5", iterations=200)
    assert result["delta"] == 0.0
    assert not result["significant"]


def test_paired_bootstrap_rejects_mismatched_reports(pipeline):
    from ragkit.evaluation import evaluate

    a = evaluate(pipeline, load_queries(QUERIES, split="dev"))
    b = evaluate(pipeline, load_queries(QUERIES, split="test"))
    with pytest.raises(ValueError):
        paired_bootstrap(a, b, "recall@5")


# ------------------------------------------------------------------- pipeline


def test_pipeline_stats_are_sane(pipeline):
    stats = pipeline.stats()
    assert stats["chunks"] > stats["documents"]
    assert stats["embedding_dim"] == 1024


def test_with_config_reuses_the_index(pipeline):
    variant = pipeline.with_config(PipelineConfig(top_k=3, use_rerank=False))
    assert variant.dense is pipeline.dense
    assert variant.bm25 is pipeline.bm25


def test_with_config_rejects_a_different_chunk_config(pipeline):
    with pytest.raises(ValueError, match="chunk config"):
        pipeline.with_config(PipelineConfig(chunk=ChunkConfig(target_words=999)))


def test_tool_fetch_document_round_trips(pipeline):
    doc = pipeline.tool_fetch_document("adr-0012-idempotency-keys")
    assert doc["title"].startswith("ADR-0012")


def test_tool_fetch_document_gives_a_recoverable_error(pipeline):
    with pytest.raises(ToolExecutionError, match="search_docs"):
        pipeline.tool_fetch_document("no-such-document")


def test_tool_list_documents_filters(pipeline):
    adrs = pipeline.tool_list_documents(doc_type="adr")
    assert adrs and all(d["doc_type"] == "adr" for d in adrs)


def test_tool_define_term_finds_a_glossary_entry(pipeline):
    assert pipeline.tool_define_term("brownout")["found"]


def test_tool_define_term_reports_a_miss_without_inventing(pipeline):
    result = pipeline.tool_define_term("quantum flux capacitor")
    assert not result["found"] and result["definition"] is None
