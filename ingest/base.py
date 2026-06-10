"""Ingestion framework base classes.

Offline only - never imported by core/api.py or any runtime module.
Jurisdictions implement ScraperBase to produce chunks conforming to
schemas/qdrant_payload.schema.json.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Chunk:
    """A single ingestion chunk conforming to qdrant_payload.schema.json."""
    document_id: str
    court: str
    court_name: str
    title: str
    date: str           # ISO 8601
    url: str
    text: str
    source_type: str    # "case" | "legislation" | "regulation" | "guidance"
    citation: str = ""
    chunk_index: int = 0
    extra: dict = field(default_factory=dict)  # jurisdiction-specific payload fields

    def to_payload(self) -> dict:
        d = {
            "document_id": self.document_id,
            "court": self.court,
            "court_name": self.court_name,
            "title": self.title,
            "date": self.date,
            "url": self.url,
            "text": self.text,
            "source_type": self.source_type,
            "chunk_index": self.chunk_index,
        }
        if self.citation:
            d["citation"] = self.citation
        d.update(self.extra)
        return d


class ScraperBase(ABC):
    """Base class for jurisdiction-specific scrapers.

    A scraper fetches source documents and yields Chunk objects.
    The IngestPipeline handles embedding and Qdrant upsert.
    """

    @abstractmethod
    def iter_chunks(self, **kwargs) -> "Iterator[Chunk]":
        """Yield chunks ready for embedding and upsert.

        Implementations should handle:
        - Fetching HTML/PDF from source URLs
        - Parsing and extracting text, metadata
        - Chunking into ~120-word windows
        - Generating stable document_id values (recommend UUID5)
        """


class ChunkerBase(ABC):
    """Base class for text chunkers."""

    @abstractmethod
    def chunk(self, text: str, metadata: dict) -> list[Chunk]:
        """Split document text into chunks with shared metadata."""


class IngestPipeline:
    """Orchestrates scrape -> chunk -> embed -> upsert.

    TODO: port from nz-legal-rag ingest/pipeline.py in Milestone 0.
    Provides crash-resumable progress tracking via a per-court-year
    progress file.
    """

    def __init__(self, scraper: ScraperBase, collection: str):
        self.scraper = scraper
        self.collection = collection

    def run(self, **kwargs) -> None:
        raise NotImplementedError("Port from nz-legal-rag - Milestone 0")
