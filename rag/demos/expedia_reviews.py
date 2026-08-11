#!/usr/bin/env python3
"""A second, different RAG: hotel review Q&A (an Expedia-shaped problem).

    python demos/expedia_reviews.py

The documentation corpus in this repo has one answer per question, written once,
by someone who knew the answer. Hotel reviews have none of those properties, and
four things change as a result:

1. THE FILTER IS THE PRIMARY INDEX, NOT THE VECTOR INDEX.
   "Is the pool open at the Gran Via?" must never return a review of a different
   hotel. hotel_id is a correctness constraint, not a relevance signal. It is
   applied first and it eliminates 99.999% of the corpus before ranking starts.

2. NO SINGLE DOCUMENT IS THE ANSWER.
   One review saying "the pool was closed" is not an answer — it is one data
   point. The answer is the distribution: how many recent reviewers said what.
   So retrieval returns evidence, and the answer layer counts it.

3. CONTRADICTION IS THE DATA, NOT A BUG.
   In documentation, two pages disagreeing is a defect to fix. In reviews,
   disagreement is the honest state of the world and the answer must show it.

4. RECENCY IS PART OF RELEVANCE.
   A 2019 review of a hotel renovated in 2024 is actively misleading. Age decay
   belongs in the ranking, not as an afterthought.

Everything else — chunking, BM25, embeddings, fusion — is reused unchanged from
`ragkit`, which is the point: the retrieval core is domain-independent. What
changes is the layer above it.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ragkit import (  # noqa: E402
    BM25Index,
    DenseIndex,
    HashingEmbedder,
    MetadataFilter,
)
from ragkit.hybrid import HybridRetriever  # noqa: E402
from ragkit.textutil import sentences, tokenize  # noqa: E402
from ragkit.types import Chunk  # noqa: E402

TODAY = dt.date(2026, 8, 11)

# --------------------------------------------------------------------- data
# (hotel_id, review_id, date, rating, traveller_type, text)
REVIEWS = [
    # --- Gran Via Barcelona: pool closed for refurbishment since spring 2026 ---
    ("gran-via-bcn", "r001", "2026-07-28", 3, "family",
     "Rooms were spotless and the staff could not have been kinder. The rooftop pool "
     "is closed for refurbishment though, which the booking page did not mention."),
    ("gran-via-bcn", "r002", "2026-07-14", 4, "couple",
     "Great location, ten minutes to the Ramblas on foot. Pool still shut when we "
     "visited. Breakfast was excellent and included."),
    ("gran-via-bcn", "r003", "2026-06-30", 5, "family",
     "Superb for kids. Connecting rooms were easy to arrange and there is a small "
     "play area on the fourth floor. Cots provided free of charge."),
    ("gran-via-bcn", "r004", "2026-06-02", 2, "business",
     "Air conditioning in room 812 barely worked and it was 34 degrees. Front desk "
     "moved us after two nights. Wifi was fast."),
    ("gran-via-bcn", "r005", "2026-05-19", 4, "family",
     "Travelled with a toddler and a five year old. Highchairs in the restaurant, "
     "and the buffet had plain pasta which saved us. Lifts are slow at breakfast."),
    ("gran-via-bcn", "r006", "2026-04-11", 5, "couple",
     "The rooftop pool was the highlight, wonderful views over the city at sunset."),
    ("gran-via-bcn", "r007", "2025-09-08", 5, "family",
     "Pool was open and heated, our children spent every afternoon in it. "
     "Rooms a little small but very clean."),
    ("gran-via-bcn", "r008", "2026-07-02", 3, "solo",
     "Street noise from the avenue is noticeable on the lower floors until about "
     "midnight. Ask for a room above the seventh floor."),
    ("gran-via-bcn", "r009", "2026-06-21", 4, "couple",
     "Quiet room at the back, we slept fine. Traffic noise at the front is real "
     "but the double glazing helps."),
    ("gran-via-bcn", "r010", "2026-03-15", 4, "family",
     "Good family base. Metro is two minutes away and the concierge booked us a "
     "car seat taxi to the aquarium."),

    # --- Marina Suites Lisbon ---
    ("marina-lis", "r101", "2026-07-20", 5, "family",
     "The kids club runs from ten until four and is genuinely well staffed. "
     "The pool has a shallow end with a lifeguard."),
    ("marina-lis", "r102", "2026-06-18", 4, "couple",
     "Beautiful sea views. Breakfast is not included and is overpriced at 28 euros."),
    ("marina-lis", "r103", "2026-05-30", 2, "family",
     "Lifts broke on day two and we were on the ninth floor with a pushchair. "
     "Took four days to fix. Staff apologetic but nothing was offered."),

    # --- Alpine Lodge Innsbruck ---
    ("alpine-inn", "r201", "2026-02-14", 5, "couple",
     "Ski storage is heated and the boot room is right by the lift pass office."),
    ("alpine-inn", "r202", "2026-01-22", 4, "family",
     "Excellent for a ski week with children. The nursery slope is a two minute walk."),
]

HOTEL_NAMES = {
    "gran-via-bcn": "Gran Via Barcelona",
    "marina-lis": "Marina Suites Lisbon",
    "alpine-inn": "Alpine Lodge Innsbruck",
}


# ================================================== 1. CHUNK: one review = one chunk
# Reviews are already short and self-contained, so there is nothing to split.
# The chunking decision here is the opposite of the documentation case: DON'T.
# Splitting a review would separate "the pool is closed" from "we still loved it"
# and both halves would mislead.


def build_chunks():
    chunks = []
    for hotel_id, rid, date, rating, traveller, text in REVIEWS:
        age_days = (TODAY - dt.date.fromisoformat(date)).days
        chunks.append(Chunk(
            chunk_id=rid,
            doc_id=hotel_id,
            ordinal=0,
            body=text,
            # The prefix is metadata the searcher may actually phrase a query in:
            # "family reviews", "recent". Indexing it makes those queries work.
            context_prefix=f"{HOTEL_NAMES[hotel_id]} review by a {traveller} traveller",
            metadata={
                "hotel_id": hotel_id, "rating": rating, "traveller": traveller,
                "date": date, "age_days": age_days, "title": HOTEL_NAMES[hotel_id],
                "tags": [traveller],
            },
        ))
    return chunks


# ============================================ 2. RECENCY: age is part of relevance


def recency_weight(age_days: int, half_life_days: int = 365) -> float:
    """A review loses half its weight every year. Never reaches zero."""
    return 0.5 ** (age_days / half_life_days)


# ================================================ 3. AGGREGATE: count the evidence
# The core difference from documentation RAG. We are not looking for THE passage
# that answers the question; we are measuring what recent reviewers said.

POSITIVE = {"open", "heated", "great", "excellent", "wonderful", "superb", "good",
            "highlight", "kind", "kinder", "clean", "spotless", "well", "fine", "easy"}
NEGATIVE = {"closed", "shut", "broke", "broken", "not", "no", "barely", "noise",
            "noisy", "slow", "small", "overpriced", "poor", "problem", "refurbishment"}


def stance(sentence: str) -> str:
    t = set(tokenize(sentence))
    pos, neg = len(t & POSITIVE), len(t & NEGATIVE)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def aggregate(query, scored, idf, min_evidence=2):
    """-> (verdict, evidence list). Counts stance across matching sentences.

    The overlap must include at least one INFORMATIVE term. Without this gate,
    "is there a spa" matches any sentence containing the word "there" and the
    system reports evidence it does not have — a junk match dressed up as
    support. IDF is already computed by the BM25 index; reuse it.
    """
    q = set(tokenize(query))
    informative = max((idf.get(t, 0.0) for t in q), default=0.0) * 0.5
    evidence = []
    for sc in scored:
        for sent in sentences(sc.chunk.body):
            overlap = q & set(tokenize(sent))
            if not overlap or max(idf.get(t, 0.0) for t in overlap) < informative:
                continue
            evidence.append({
                "review": sc.chunk_id,
                "date": sc.chunk.metadata["date"],
                "traveller": sc.chunk.metadata["traveller"],
                "weight": recency_weight(sc.chunk.metadata["age_days"]),
                "stance": stance(sent),
                "text": sent.strip(),
            })
    if not evidence:
        # Retrieval found candidates but the lexical extractor matched none of
        # them. That is a DIFFERENT failure from "nothing is relevant", and
        # conflating the two hides a real gap — see the note printed below.
        return "unconfirmed", evidence
    if len(evidence) < min_evidence:
        return "insufficient_evidence", evidence
    tally = Counter(e["stance"] for e in evidence)
    weighted = {s: sum(e["weight"] for e in evidence if e["stance"] == s)
                for s in ("positive", "negative", "neutral")}
    top = max(weighted, key=weighted.get)
    contested = (min(weighted["positive"], weighted["negative"]) >
                 0.35 * max(weighted["positive"], weighted["negative"]))
    return ("contested" if contested else top), evidence


# ==================================================================== 4. THE FLOW


def ask(retriever, chunks, idf, query, hotel_id, traveller=None, k=8):
    print(f"\n{'=' * 76}")
    print(f"Q: {query}")
    print(f"   hotel={hotel_id}" + (f"  traveller={traveller}" if traveller else ""))
    print("=" * 76)

    # -- STEP 1: the HARD filter. Non-negotiable, applied before any ranking. --
    equals = {"hotel_id": hotel_id}
    if traveller:
        equals["traveller"] = traveller
    mfilter = MetadataFilter(equals=equals)
    pool = [c for c in chunks if mfilter.matches(c)]
    print(f"\n  1. hard filter      {len(chunks)} reviews -> {len(pool)} for this hotel")
    print("     (a review of another hotel is WRONG, not merely irrelevant —")
    print("      so this is a correctness gate, applied before ranking)")

    if not pool:
        print("\n  ABSTAIN: no reviews match that filter.")
        return

    # -- STEP 2: hybrid retrieval, unchanged from the documentation pipeline --
    hits = retriever.search(query, k, metadata_filter=mfilter)
    print(f"  2. hybrid retrieve  {len(hits)} candidates (BM25 + dense, RRF fused)")

    # -- STEP 3: recency re-weighting ------------------------------------------
    for sc in hits:
        w = recency_weight(sc.chunk.metadata["age_days"])
        sc.components["recency"] = w
        sc.score *= w
    hits.sort(key=lambda s: -s.score)
    print("  3. recency decay    half-life 365 days")
    for sc in hits[:4]:
        print(f"       {sc.chunk_id}  {sc.chunk.metadata['date']}  "
              f"x{sc.components['recency']:.2f} -> {sc.score:.4f}")

    # -- STEP 4: aggregate rather than quote -----------------------------------
    verdict, evidence = aggregate(query, hits, idf)
    print(f"  4. aggregate        {len(evidence)} matching statements")

    # -- STEP 5: the answer ----------------------------------------------------
    print(f"\n  VERDICT: {verdict.upper().replace('_', ' ')}")
    if verdict == "unconfirmed":
        print("  Retrieval found relevant-looking reviews, but the evidence extractor")
        print("  matched none of them: it works on exact word overlap, and the query")
        print("  words are absent from the corpus. Showing the retrieved reviews")
        print("  UNCONFIRMED rather than claiming nobody mentioned it.")
        for sc in hits[:3]:
            print(f"    [retrieved] {sc.chunk.metadata['date']}  "
                  f"\"{sc.chunk.body[:62]}\"")
    elif verdict == "insufficient_evidence":
        n = len(evidence)
        print(f"  Only {n} review{'s' if n != 1 else ''} mention{'' if n != 1 else 's'} this. "
              "Not enough to generalise —")
        print("  showing what was said rather than drawing a conclusion.")
    for e in sorted(evidence, key=lambda e: -e["weight"])[:4]:
        print(f"    [{e['stance']:<8}] {e['date']}  {e['traveller']:<8} "
              f"w={e['weight']:.2f}  \"{e['text'][:62]}\"")
    if verdict == "contested":
        print("\n  Reviewers disagree. In a review corpus that is the honest answer —")
        print("  note the dates: the disagreement here is a change over time, not noise.")


def main():
    chunks = build_chunks()
    embedder = HashingEmbedder(dim=512)
    embedder.fit(c.text for c in chunks)
    bm25 = BM25Index(chunks)
    retriever = HybridRetriever(
        bm25=bm25,
        dense=DenseIndex(chunks, embedder),
        method="rrf",
        candidate_k=20,
    )
    idf = dict(bm25._bm25.idf) if hasattr(bm25._bm25, "idf") else {}
    print(f"indexed {len(chunks)} reviews across {len(HOTEL_NAMES)} hotels")

    ask(retriever, chunks, idf, "is the pool open", "gran-via-bcn")
    ask(retriever, chunks, idf, "is it good for a toddler", "gran-via-bcn", traveller="family")
    ask(retriever, chunks, idf, "is it noisy at night", "gran-via-bcn")
    ask(retriever, chunks, idf, "is there a spa", "gran-via-bcn")

    print(f"\n{'=' * 76}")
    print("WHAT THIS CORPUS TEACHES THAT THE DOCS CORPUS COULD NOT")
    print("=" * 76)
    print("""
  The pool question is CONTESTED, and look at why: reviews from 2025 and April
  2026 say it was open and lovely; reviews from May onward say it is closed for
  refurbishment. Both sets are truthful. A docs-style RAG that picks the single
  best-matching passage would confidently return either one and be wrong half
  the time.

  Recency decay is what resolves it — but decay alone would just bury the old
  reviews. Surfacing the dates is what makes the answer USEFUL: "closed since
  around May 2026" is better than either "open" or "closed".

  The "noisy at night" question is the sharpest lesson. Retrieval NAILED it —
  r008 says street noise is noticeable "until about midnight". But the evidence
  extractor found nothing, because the query says "noisy"/"night" and the review
  says "noise"/"midnight". Dense retrieval crossed that gap on character
  n-grams; the lexical extractor could not. It is the same split you saw with
  the typo query: retrieval tolerates word-form mismatch, extraction does not,
  and an LLM generator is what bridges it. A production system would use one
  here — the extractor is the offline stand-in.

  And the spa question returns insufficient evidence rather than an answer.
  With one hotel and ten reviews, the difference between "no spa" and "nobody
  mentioned the spa" is not something retrieval can determine — so it must not
  pretend to.
""")


if __name__ == "__main__":
    main()
