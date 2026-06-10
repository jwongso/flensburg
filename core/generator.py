"""LLM generation via OpenAI-compatible API (llama.cpp, Ollama, vLLM, LM Studio)."""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
_LLM_MODEL = os.getenv("LLM_MODEL", "qwen3")
_LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2500"))
_LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))


class Generator:
    def __init__(self, system_prompt: str) -> None:
        self._client = httpx.AsyncClient(base_url=_LLM_BASE_URL, timeout=120)
        self._system_prompt = system_prompt

    async def generate_stream(
        self,
        question: str,
        context_chunks: list[str],
        sources: list[dict],
        legislation_anchor: str | None = None,
        thinking: bool = False,
    ) -> AsyncIterator[str]:
        truncated = [c[:1500] for c in context_chunks]
        context_block = "\n\n---\n\n".join(f"[S{i + 1}] {chunk}" for i, chunk in enumerate(truncated))
        source_header = "\n".join(
            f"  [S{i + 1}] {s.get('title') or s.get('case_id', 'Unknown')} | "
            f"{s.get('court_name', '')} | {s.get('date', '')} | {s.get('url', '')}"
            for i, s in enumerate(sources)
        )
        anchor_block = f"{legislation_anchor}\n\n---\n\n" if legislation_anchor else ""
        user_message = (
            f"{anchor_block}"
            f"Source index:\n{source_header}\n\n"
            f"Context documents:\n\n{context_block}\n\n"
            f"---\n\nQuestion: {question}"
        )
        payload = {
            "model": _LLM_MODEL,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": _LLM_MAX_TOKENS,
            "temperature": _LLM_TEMPERATURE,
            "chat_template_kwargs": {"enable_thinking": thinking},
            "stream": True,
        }
        async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    token = chunk["choices"][0]["delta"].get("content", "")
                    if token:
                        yield token
                except (KeyError, json.JSONDecodeError):
                    continue

    async def close(self) -> None:
        await self._client.aclose()
