from __future__ import annotations

from datetime import UTC, datetime

from ragx.config import GenerationConfig, RerankConfig
from ragx.generate.answerer import Answerer
from ragx.generate.citations import verify
from ragx.generate.prompt import ABSTAIN_TOKEN, build_user_prompt
from ragx.llm import LLMResponse, ScriptedLLM
from ragx.rerank.llm_reranker import LLMReranker
from ragx.types import Chunk, ScoredChunk, Usage


def _context(marker_text: str, chunk_id: str, doc_id: str = "d1") -> ScoredChunk:
    chunk = Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        tenant_id="acme",
        text=marker_text,
        embed_text=marker_text,
        title="Expense Policy",
        uri="https://example.test/expenses",
        heading_path=("Submitting a claim",),
        updated_at=datetime(2026, 5, 4, tzinfo=UTC),
    )
    return ScoredChunk(chunk=chunk, score=1.0)


CONTEXTS = [
    _context("Expense claims must be submitted within 30 days of the expense date.", "c1"),
    _context("Claims older than 90 days will not be reimbursed at all.", "c2"),
]


# ---- prompt ----------------------------------------------------------------


def test_prompt_numbers_contexts_and_includes_metadata():
    prompt = build_user_prompt("How long do I have?", CONTEXTS)
    assert "[1] Expense Policy › Submitting a claim" in prompt
    assert "updated: 2026-05-04" in prompt
    assert "[2]" in prompt


def test_prompt_with_no_contexts_forces_abstention():
    prompt = build_user_prompt("Anything?", [])
    assert ABSTAIN_TOKEN in prompt


# ---- citation verification -------------------------------------------------


def test_valid_citations_score_full_grounding():
    text = "Expense claims must be submitted within 30 days of the expense date. [1]"
    report = verify(text, CONTEXTS)
    assert report.grounding_score == 1.0
    assert [c.chunk_id for c in report.citations] == ["c1"]
    assert not report.invalid_markers


def test_hallucinated_marker_is_dropped_and_reported():
    text = "Expense claims must be submitted within 30 days of the expense date. [7]"
    report = verify(text, CONTEXTS)
    assert report.invalid_markers == (7,)
    assert report.citations == ()
    assert report.grounding_score == 0.0


def test_uncited_claim_lowers_the_grounding_score():
    text = (
        "Expense claims must be submitted within 30 days of the expense date. [1] "
        "Claims are always paid out on the fifteenth of the following month."
    )
    report = verify(text, CONTEXTS)
    assert report.grounding_score == 0.5
    assert len(report.uncited_sentences) == 1


def test_short_non_claim_sentences_do_not_need_citations():
    text = "Yes. Expense claims must be submitted within 30 days of the expense date. [1]"
    report = verify(text, CONTEXTS)
    assert report.grounding_score == 1.0


def test_citation_pointing_at_an_unrelated_passage_is_flagged_as_weak():
    text = "The office cafeteria serves hot lunch between twelve and two every weekday. [1]"
    report = verify(text, CONTEXTS)
    assert report.weakly_supported


def test_citation_carries_a_verifiable_quote():
    text = "Expense claims must be submitted within 30 days of the expense date. [1]"
    report = verify(text, CONTEXTS)
    assert report.citations[0].quote is not None
    assert "30 days" in report.citations[0].quote


# ---- answerer --------------------------------------------------------------


def test_answer_is_returned_with_validated_citations():
    llm = ScriptedLLM(lambda model, prompt: "Within 30 days of the expense date. [1]")
    answer = Answerer(llm, GenerationConfig()).answer("How long?", CONTEXTS)
    assert not answer.abstained
    assert answer.grounding_score == 1.0
    assert answer.citations[0].uri == "https://example.test/expenses"


def test_abstention_is_detected_and_not_retried():
    llm = ScriptedLLM(lambda model, prompt: f"{ABSTAIN_TOKEN}\nThe sources do not say.")
    answer = Answerer(llm, GenerationConfig()).answer("Parental leave?", CONTEXTS)
    assert answer.abstained
    assert len(llm.calls) == 1


def test_ungrounded_answer_triggers_exactly_one_stricter_retry():
    def handler(model: str, prompt: str) -> str:
        if "previous attempt contained sentences with no citation" in prompt:
            return "Expense claims must be submitted within 30 days of the expense date. [1]"
        return "Claims are reimbursed on the fifteenth of the month after submission."

    llm = ScriptedLLM(handler)
    answer = Answerer(llm, GenerationConfig()).answer("How long?", CONTEXTS)
    assert len(llm.calls) == 2
    assert answer.grounding_score == 1.0


def test_retry_that_does_not_improve_keeps_the_first_answer():
    llm = ScriptedLLM(lambda model, prompt: "Reimbursement happens whenever finance gets to it.")
    answer = Answerer(llm, GenerationConfig()).answer("How long?", CONTEXTS)
    assert len(llm.calls) == 2
    assert answer.grounding_score == 0.0
    assert "Reimbursement happens" in answer.text


def test_model_refusal_becomes_a_clean_abstention_not_a_crash():
    llm = ScriptedLLM(
        lambda model, prompt: LLMResponse(text="", usage=Usage(), stop_reason="refusal")
    )
    answer = Answerer(llm, GenerationConfig()).answer("something", CONTEXTS)
    assert answer.refusal and answer.abstained
    assert answer.text  # a user-facing message, not an empty string


def test_streaming_yields_tokens_then_a_verified_answer():
    llm = ScriptedLLM(lambda model, prompt: "Within 30 days of the expense date. [1]")
    events = list(Answerer(llm, GenerationConfig()).stream_answer("How long?", CONTEXTS))
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "token" and kinds[-1] == "done"
    final = events[-1][1]
    assert final.citations and final.grounding_score == 1.0


# ---- reranker --------------------------------------------------------------


def _candidates(n: int) -> list[ScoredChunk]:
    return [
        ScoredChunk(chunk=_context(f"passage {i}", f"c{i}").chunk, score=1.0 / (i + 1))
        for i in range(n)
    ]


def test_llm_reranker_reorders_by_returned_scores():
    def handler(model: str, prompt: str) -> str:
        return '{"scores": [{"id": 1, "score": 2}, {"id": 2, "score": 9}, {"id": 3, "score": 4}]}'

    reranker = LLMReranker(ScriptedLLM(handler), "claude-opus-5", RerankConfig())
    ranked = reranker.rerank("q", _candidates(3), k=3)
    assert [c.chunk.chunk_id for c in ranked] == ["c1", "c2", "c0"]


def test_llm_reranker_fails_open_to_fusion_order():
    def handler(model: str, prompt: str) -> str:
        raise TimeoutError("reranker is down")

    reranker = LLMReranker(ScriptedLLM(handler), "claude-opus-5", RerankConfig())
    ranked = reranker.rerank("q", _candidates(3), k=3)
    assert [c.chunk.chunk_id for c in ranked] == ["c0", "c1", "c2"]


def test_llm_reranker_can_be_configured_to_fail_closed():
    import pytest

    def handler(model: str, prompt: str) -> str:
        raise TimeoutError("reranker is down")

    reranker = LLMReranker(
        ScriptedLLM(handler), "claude-opus-5", RerankConfig(fail_open=False)
    )
    with pytest.raises(TimeoutError):
        reranker.rerank("q", _candidates(3), k=3)


def test_llm_reranker_caches_by_query_and_candidate_set():
    calls: list[str] = []

    def handler(model: str, prompt: str) -> str:
        calls.append(prompt)
        return '{"scores": [{"id": 1, "score": 5}, {"id": 2, "score": 1}]}'

    reranker = LLMReranker(ScriptedLLM(handler), "claude-opus-5", RerankConfig())
    candidates = _candidates(2)
    reranker.rerank("same question", candidates, k=2)
    reranker.rerank("same question", candidates, k=2)
    assert len(calls) == 1


def test_reranker_batches_are_normalised_before_merging():
    # Batch 1 is scored generously (8,9); batch 2 harshly (1,2). Without
    # normalisation the top of batch 1 would always win.
    def handler(model: str, prompt: str) -> str:
        if "passage 0" in prompt:
            return '{"scores": [{"id": 1, "score": 8}, {"id": 2, "score": 9}]}'
        return '{"scores": [{"id": 1, "score": 1}, {"id": 2, "score": 2}]}'

    reranker = LLMReranker(
        ScriptedLLM(handler), "claude-opus-5", RerankConfig(batch_size=2), max_workers=1
    )
    ranked = reranker.rerank("q", _candidates(4), k=4)
    # Within each batch the second item scored higher, so both winners rank above
    # both losers rather than batch 1 sweeping the top.
    top_two = {c.chunk.chunk_id for c in ranked[:2]}
    assert top_two == {"c1", "c3"}


def test_reranker_puts_unscored_candidates_last_without_losing_them():
    def handler(model: str, prompt: str) -> str:
        return '{"scores": [{"id": 2, "score": 7}]}'

    reranker = LLMReranker(ScriptedLLM(handler), "claude-opus-5", RerankConfig())
    ranked = reranker.rerank("q", _candidates(3), k=3)
    assert ranked[0].chunk.chunk_id == "c1"
    assert len(ranked) == 3
