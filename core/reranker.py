"""Cross-encoder relevance gate for legislation retrieval precision.

Architecture: bi-encoder (Qdrant) handles recall; cross-encoder handles precision.

Bi-encoder retrieval embeds query and passage independently, so token-level overlap
can surface false positives - "security camera" pulls in s18A (bond/security deposit)
because "security" appears in both. A cross-encoder sees (query, passage) together
and attends to full context, eliminating these word-sense collisions without keyword
rules.

Active gate (score_and_filter):
  - Scores all candidates with BAAI/bge-reranker-v2-m3
  - Drops non-forced candidates below leg_ce_min_score (default 0.15)
  - Re-orders remaining: forced sections first (route-order preserved), then by
    CE score descending
  - Route-forced sections always pass regardless of score (recall guarantee)

Scores are sigmoid-normalised [0, 1]:
  - Clearly relevant:   > 0.5
  - Borderline:         0.15 - 0.5
  - Clearly irrelevant: < 0.15  (dropped)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

LEG_CE_MIN_SCORE = 0.15  # default threshold; override per-jurisdiction via leg_ce_min_score


class CrossEncoderReranker:
    """Wraps BAAI/bge-reranker-v2-m3 for (query, passage) relevance scoring.

    Loaded lazily at first call; warmed up immediately after load to amortise
    JIT overhead across subsequent queries. Always runs on CPU - GPU is
    reserved for the generation model.
    """

    def __init__(
        self,
        model: str = "BAAI/bge-reranker-v2-m3",
        max_length: int = 512,
    ) -> None:
        self._model_name = model
        self._max_length = max_length
        self._model: Any = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import CrossEncoder
        self._model = CrossEncoder(self._model_name, max_length=self._max_length, device="cpu")
        try:
            self._model.predict([("warmup", "warmup")])
        except Exception:
            pass

    def score(
        self,
        query: str,
        candidates: list,
    ) -> list[tuple[Any, float]]:
        """Score each candidate without changing their order.

        Returns list of (candidate, ce_score) in original input order.
        Scores are in [0, 1] (sigmoid-normalised).
        """
        if not candidates:
            return []
        try:
            self._ensure_loaded()
            pairs = [(query, c.text) for c in candidates]
            scores = self._model.predict(pairs)
            raw = scores.tolist() if hasattr(scores, "tolist") else list(scores)
            return list(zip(candidates, raw))
        except Exception as exc:
            logger.warning("CrossEncoderReranker.score error: %s", exc)
            return [(c, 0.0) for c in candidates]

    def score_and_filter(
        self,
        query: str,
        candidates: list,
        min_score: float = LEG_CE_MIN_SCORE,
        always_keep: set[str] | None = None,
    ) -> tuple[list, list[dict]]:
        """Score all candidates, drop irrelevant ones, reorder by relevance.

        Candidates whose case_id is in always_keep (route-forced sections) are
        retained unconditionally - they represent recall guarantees from the route
        table and must never be dropped by the precision gate.

        Returns:
            (filtered_candidates, score_log)

            filtered_candidates: forced sections first (original route order
              preserved), then remaining by CE score descending.
            score_log: list of dicts with case_id, ce_score, forced, kept flags
              for route_debug logging.
        """
        if not candidates:
            return [], []
        keep_ids = always_keep or set()
        scored = self.score(query, candidates)

        score_log = [
            {
                "case_id": c.case_id,
                "ce_score": round(float(s), 4),
                "forced": c.case_id in keep_ids,
                "kept": c.case_id in keep_ids or s >= min_score,
            }
            for c, s in scored
        ]

        # Forced sections: preserve original route-determined order
        forced = [c for c, _ in scored if c.case_id in keep_ids]

        # Non-forced: drop below threshold, sort remainder by score desc
        voluntary = sorted(
            [(c, s) for c, s in scored if c.case_id not in keep_ids and s >= min_score],
            key=lambda x: x[1],
            reverse=True,
        )

        return forced + [c for c, _ in voluntary], score_log

    def rerank(
        self,
        query: str,
        candidates: list,
        top_k: int | None = None,
    ) -> list:
        """Re-order candidates by cross-encoder score (highest first)."""
        if not candidates:
            return candidates
        scored = self.score(query, candidates)
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [c for c, _ in scored]
        return result[:top_k] if top_k is not None else result
