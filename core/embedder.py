"""Sentence-transformer embedder with GPU/CPU device selection."""

from __future__ import annotations

import torch
from sentence_transformers import SentenceTransformer

_MODEL_CONFIGS: dict[str, dict] = {
    "nomic-ai/nomic-embed-text-v1.5": {
        "query_prefix": "search_query: ",
        "doc_prefix": "search_document: ",
        "trust_remote_code": True,
    },
    "BAAI/bge-m3": {
        "query_prefix": "",
        "doc_prefix": "",
        "trust_remote_code": False,
    },
    "intfloat/e5-large-v2": {
        "query_prefix": "query: ",
        "doc_prefix": "passage: ",
        "trust_remote_code": False,
    },
    "Qwen/Qwen3-Embedding-0.6B": {
        "query_prompt_name": "query",
        "doc_prefix": "",
        "trust_remote_code": True,
    },
}

_DEFAULT_MODEL = "nomic-ai/nomic-embed-text-v1.5"


def _select_device(min_vram_mb: int = 512) -> str:
    if not torch.cuda.is_available():
        return "cpu"
    try:
        free_bytes, _ = torch.cuda.mem_get_info()
        return "cuda" if free_bytes >= min_vram_mb * 1024 * 1024 else "cpu"
    except Exception:
        return "cpu"


class Embedder:
    def __init__(self, model_name: str | None = None, device: str | None = None) -> None:
        self._model_name = model_name or _DEFAULT_MODEL
        cfg = _MODEL_CONFIGS.get(self._model_name, {
            "query_prefix": "",
            "doc_prefix": "",
            "trust_remote_code": False,
        })
        self._query_prefix: str = cfg.get("query_prefix", "")
        self._doc_prefix: str = cfg.get("doc_prefix", "")
        self._query_prompt_name: str | None = cfg.get("query_prompt_name")
        self._model = SentenceTransformer(
            self._model_name,
            trust_remote_code=cfg.get("trust_remote_code", False),
            device=device or _select_device(),
        )

    @property
    def dim(self) -> int:
        return self._model.get_embedding_dimension()

    def _encode(self, texts: list[str], prefix: str = "", prompt_name: str | None = None) -> list[list[float]]:
        if prompt_name:
            vecs = self._model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                prompt_name=prompt_name,
            )
        else:
            vecs = self._model.encode(
                [prefix + t for t in texts],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        return vecs.tolist()

    def encode_documents(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            results.extend(self._encode(texts[i: i + batch_size], prefix=self._doc_prefix))
        return results

    async def embed(self, text: str) -> list[float]:
        return self._encode([text], prefix=self._query_prefix, prompt_name=self._query_prompt_name)[0]

    async def embed_batch(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            results.extend(self._encode(texts[i: i + batch_size], prefix=self._doc_prefix))
        return results

    async def close(self) -> None:
        pass
