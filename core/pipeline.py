"""RAG pipeline: embed -> retrieve -> deduplicate -> generate."""

from __future__ import annotations

import os

from core.embedder import Embedder
from core.generator import Generator
from core.retriever import SearchResult, VectorStore

_TOP_K = int(os.getenv("TOP_K", "5"))

# Score multipliers for manually ingested secondary sources.
# legislation/case_law/official_policy: no discount - authoritative sources.
# law_review/advocacy_submission: 0.85 - useful context, should not outrank tribunal cases.
# commercial_commentary: 0.80 - lowest priority, treat as background only.
_MANUAL_DISCOUNTS: dict[str, float] = {
    "law_review": 0.85,
    "advocacy_submission": 0.85,
    "community_legal_guidance": 0.85,
    "commercial_commentary": 0.80,
}


def _apply_manual_discounts(hits: list[SearchResult]) -> list[SearchResult]:
    for h in hits:
        if h.payload.get("court") == "MANUAL":
            discount = _MANUAL_DISCOUNTS.get(h.payload.get("source_type", ""), 1.0)
            if discount != 1.0:
                h.score = h.score * discount
    return hits


def _deduplicate(hits: list[SearchResult], top_k: int) -> list[SearchResult]:
    seen: dict[str, SearchResult] = {}
    for h in hits:
        cid = h.case_id
        if cid not in seen or h.score > seen[cid].score:
            seen[cid] = h
    return sorted(seen.values(), key=lambda x: x.score, reverse=True)[:top_k]


def _mmr_select(hits: list[SearchResult], top_k: int, lambda_: float = 0.6) -> list[SearchResult]:
    selected: list[SearchResult] = []
    remaining = list(hits)
    while len(selected) < top_k and remaining:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        best_score = -float("inf")
        best_i = 0
        for i, h in enumerate(remaining):
            h_words = set(h.text.lower().split())
            max_sim = max(
                len(h_words & set(s.text.lower().split()))
                / max(len(h_words | set(s.text.lower().split())), 1)
                for s in selected
            )
            mmr = lambda_ * h.score - (1.0 - lambda_) * max_sim
            if mmr > best_score:
                best_score = mmr
                best_i = i
        selected.append(remaining.pop(best_i))
    return selected


class RAGPipeline:
    def __init__(
        self,
        collection: str,
        system_prompt: str,
        courts: list[str] | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self._embedder = embedder or Embedder()
        self._store = VectorStore(collection=collection)
        self._generator = Generator(system_prompt=system_prompt)
        self._courts = courts

    async def retrieve(
        self,
        question: str,
        top_k: int = _TOP_K,
        min_score: float = 0.0,
        min_chunks: int = 1,
        strategy: str = "vector",
    ) -> tuple[list[str], list[dict]]:
        query_vector = await self._embedder.embed(question)
        raw_hits = self._store.search(query_vector, top_k=top_k * 3, courts=self._courts)
        if not raw_hits:
            return [], []

        raw_hits = _apply_manual_discounts(raw_hits)
        hits = _deduplicate(raw_hits, top_k * 2)

        if strategy == "mmr":
            hits = _mmr_select(hits, top_k)
        else:
            hits = hits[:top_k]

        if min_score > 0.0:
            hits = [h for h in hits if h.score >= min_score]
        if len(hits) < min_chunks:
            return [], []

        context_texts = [h.text for h in hits]
        sources = [
            {
                "case_id": h.case_id,
                "title": h.title,
                "court_name": h.court_name,
                "date": h.date,
                "url": h.url,
                "_score": round(h.score, 4),
            }
            for h in hits
        ]
        return context_texts, sources

    @property
    def store(self) -> VectorStore:
        return self._store

    async def embed(self, text: str) -> list[float]:
        return await self._embedder.embed(text)

    async def close(self) -> None:
        await self._embedder.close()
        await self._generator.close()
