"""JurisdictionService - shared async core between HTTP API and MCP server.

Both create_app() and create_mcp_server() can build one of these to get
consistent retrieval, sanitization, and safety behaviour regardless of
the transport used to invoke the jurisdiction.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from core.jurisdiction import JurisdictionBase
from core.pipeline import RAGPipeline
from core.retriever import VectorStore
from core.sanitize import sanitize_question

logger = logging.getLogger(__name__)

_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
_LLM_MODEL = os.getenv("LLM_MODEL", "qwen3")

_REWRITE_SYSTEM = (
    "Rewrite the following as a concise formal legal question optimised for retrieving "
    "relevant case decisions. Focus on the underlying legal dispute and claims. "
    "Output only the rewritten question, no explanation."
)

_MCP_MAX_CONCURRENT = 1


class ServiceError(Exception):
    """Raised by JurisdictionService for client-facing errors."""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


class JurisdictionService:
    """Wraps a jurisdiction's RAG pipeline for use by HTTP and MCP layers.

    Both create_app() and create_mcp_server() can build one of these, ensuring
    identical retrieval, sanitization, and safety behaviour regardless of
    the transport.
    """

    def __init__(
        self,
        jurisdiction: JurisdictionBase,
        pipeline: RAGPipeline,
        leg_store: VectorStore | None = None,
    ) -> None:
        self._jx = jurisdiction
        self._pipeline = pipeline
        self._leg_store = leg_store
        self._sem = asyncio.Semaphore(_MCP_MAX_CONCURRENT)

    # ------------------------------------------------------------------
    # Properties (available to jurisdiction.register_mcp_tools)
    # ------------------------------------------------------------------

    @property
    def jurisdiction(self) -> JurisdictionBase:
        return self._jx

    @property
    def pipeline(self) -> RAGPipeline:
        return self._pipeline

    @property
    def leg_store(self) -> VectorStore | None:
        return self._leg_store

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate(self, question: str) -> str:
        question = question.strip()
        if not question:
            raise ServiceError("Question cannot be empty.")
        if len(question) > self._jx.max_question_chars:
            raise ServiceError(
                f"Question exceeds {self._jx.max_question_chars} characters."
            )
        sanitized = sanitize_question(question, max_chars=self._jx.max_question_chars)
        if not sanitized:
            raise ServiceError("Question was rejected by content filter.")
        return sanitized

    async def _rewrite(self, question: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(
                    f"{_LLM_BASE_URL}/chat/completions",
                    json={
                        "model": _LLM_MODEL,
                        "messages": [
                            {"role": "system", "content": _REWRITE_SYSTEM},
                            {"role": "user", "content": question},
                        ],
                        "max_tokens": 100,
                        "temperature": 0.0,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                )
                r.raise_for_status()
                rewritten = r.json()["choices"][0]["message"]["content"].strip()
                return rewritten if rewritten else question
        except Exception:
            return question

    # ------------------------------------------------------------------
    # Service operations
    # ------------------------------------------------------------------

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Vector search - retrieval only, no generation."""
        query = self._validate(query)
        top_k = max(1, min(top_k, 20))
        async with self._sem:
            _, sources = await self._pipeline.retrieve(query, top_k=top_k)
        return sources

    async def ask(self, question: str) -> dict[str, Any]:
        """Full RAG: retrieve + generate. Returns {answer, sources}."""
        question = self._validate(question)
        async with self._sem:
            rewritten = await self._rewrite(question)
            context_texts, sources = await self._pipeline.retrieve(rewritten, top_k=5)
            if not context_texts:
                return {
                    "answer": "No relevant sources found for your question.",
                    "sources": [],
                }

            tokens: list[str] = []
            async for token in self._pipeline._generator.generate_stream(
                question=question,
                context_chunks=context_texts,
                sources=sources,
            ):
                tokens.append(token)

        return {"answer": "".join(tokens), "sources": sources}

    async def get_source(self, source_id: str) -> dict | None:
        """Fetch a specific case/source chunk by its ID."""
        hit = self._pipeline.store.fetch_by_case_id(source_id)
        if not hit:
            return None
        return {
            "source_id": hit.case_id,
            "title": hit.title,
            "court_name": hit.court_name,
            "date": hit.date,
            "url": hit.url,
            "text": hit.text,
        }

    async def get_legislation(self, section_id: str) -> dict | None:
        """Fetch a specific legislation section by its ID (e.g. NZLEG/RTA/s42A)."""
        if not self._leg_store:
            return None
        hit = self._leg_store.fetch_by_case_id(section_id)
        if not hit:
            return None
        return {
            "section_id": hit.case_id,
            "title": hit.title,
            "text": hit.text,
            "url": hit.url,
        }

    async def close(self) -> None:
        await self._pipeline.close()
