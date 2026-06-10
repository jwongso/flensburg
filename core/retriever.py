"""Qdrant vector store: search and upsert with optional metadata filters."""

from __future__ import annotations

import os
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HasIdCondition,
    MatchAny,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

_QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
_TOP_K_DEFAULT = int(os.getenv("TOP_K", "5"))

_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def point_id(case_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(_NS, f"{case_id}:{chunk_index}"))


class SearchResult:
    def __init__(self, payload: dict[str, Any], score: float) -> None:
        self.payload = payload
        self.score = score

    @property
    def text(self) -> str:
        return self.payload.get("text", "")

    @property
    def case_id(self) -> str:
        return self.payload.get("case_id", "")

    @property
    def title(self) -> str:
        return self.payload.get("title", "")

    @property
    def court_name(self) -> str:
        return self.payload.get("court_name", "")

    @property
    def url(self) -> str:
        return self.payload.get("url", "")

    @property
    def date(self) -> str:
        return self.payload.get("date", "")


class VectorStore:
    def __init__(self, collection: str, qdrant_url: str | None = None) -> None:
        self._client = QdrantClient(url=qdrant_url or _QDRANT_URL)
        self._collection = collection

    def ensure_collection(self, dim: int) -> None:
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            for field in ("court", "court_name", "case_id"):
                self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema="keyword",
                )
            self._client.create_payload_index(
                collection_name=self._collection,
                field_name="year",
                field_schema="integer",
            )

    def case_ids_exist(self, case_ids: list[str]) -> set[str]:
        ids = [point_id(cid, 0) for cid in case_ids]
        results = self._client.retrieve(
            collection_name=self._collection,
            ids=ids,
            with_payload=["case_id"],
        )
        return {r.payload["case_id"] for r in results}

    def upsert(self, vectors: list[list[float]], payloads: list[dict[str, Any]]) -> None:
        points = [
            PointStruct(
                id=point_id(p["case_id"], p["chunk_index"]),
                vector=v,
                payload=p,
            )
            for v, p in zip(vectors, payloads)
        ]
        self._client.upsert(collection_name=self._collection, points=points)

    def search(
        self,
        query_vector: list[float],
        top_k: int = _TOP_K_DEFAULT,
        courts: list[str] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        flags: list[str] | None = None,
    ) -> list[SearchResult]:
        must: list = []
        should: list = []

        if courts:
            must.append(FieldCondition(key="court", match=MatchAny(any=courts)))
        if year_from is not None or year_to is not None:
            must.append(FieldCondition(
                key="year",
                range=Range(
                    gte=year_from if year_from is not None else 1900,
                    lte=year_to if year_to is not None else 2100,
                ),
            ))
        if flags:
            for f in flags:
                should.append(FieldCondition(key="flags", match=MatchValue(value=f)))

        query_filter = Filter(must=must or None, should=should or None) if (must or should) else None

        hits = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        ).points
        return [SearchResult(h.payload, h.score) for h in hits]

    def fetch_by_case_id(self, case_id: str) -> "SearchResult | None":
        """Return one representative chunk for a case_id (first chunk found)."""
        results, _ = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=Filter(must=[FieldCondition(key="case_id", match=MatchValue(value=case_id))]),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        return SearchResult(results[0].payload, 1.0) if results else None

    @property
    def client(self) -> QdrantClient:
        return self._client

    @property
    def collection_name(self) -> str:
        return self._collection

    def search_filtered(
        self,
        query_vector: list[float],
        query_filter: "Filter",
        top_k: int = _TOP_K_DEFAULT,
    ) -> list[SearchResult]:
        hits = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        ).points
        return [SearchResult(h.payload, h.score) for h in hits]

    def scroll_filtered(
        self,
        query_filter,
        limit: int = 200,
    ) -> list[SearchResult]:
        raw, _ = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [SearchResult(r.payload, 1.0) for r in raw]

    def search_within(
        self,
        query_vector: list[float],
        point_ids: list[str],
        top_k: int = _TOP_K_DEFAULT,
    ) -> list[SearchResult]:
        if not point_ids:
            return []
        hits = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            query_filter=Filter(must=[HasIdCondition(has_id=point_ids)]),
            with_payload=True,
        ).points
        return [SearchResult(h.payload, h.score) for h in hits]
