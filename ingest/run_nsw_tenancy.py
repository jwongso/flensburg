"""Ingest runner for NSW NCAT decisions from AustLII.

Scrapes NSWCATCD pages via Playwright, embeds with nomic-embed-text-v1.5,
and upserts into Qdrant.

Usage:
    python -m ingest.run_nsw_tenancy
    python -m ingest.run_nsw_tenancy --years 2025 2026 --collection nsw_tenancy_ncat
    python -m ingest.run_nsw_tenancy --dry-run     # scrape only, no embed/upsert
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.browser import BrowserSession
from core.embedder import Embedder
from core.retriever import VectorStore
from jurisdictions.nsw_tenancy.scraper import NSWCATScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

EMBED_BATCH = 32        # chunks per embedding batch
UPSERT_BATCH = 64       # chunks per Qdrant upsert


async def run(years: list[int], collection: str, dry_run: bool) -> None:
    scraper = NSWCATScraper(years=years)

    async with BrowserSession() as browser:
        log.info("Scraping %d year(s): %s", len(years), years)
        chunks = await scraper.scrape(browser)

    if not chunks:
        log.warning("No chunks scraped - nothing to ingest")
        return

    log.info("Scraped %d chunks total", len(chunks))

    if dry_run:
        log.info("--dry-run set, skipping embed and upsert")
        for c in chunks[:5]:
            log.info("  Sample: %s | %s | %s chars", c["case_id"], c["date"], len(c["text"]))
        return

    embedder = Embedder()
    store = VectorStore(collection=collection)

    # Ensure collection exists
    sample_vec = await embedder.embed("test")
    store.ensure_collection(dim=len(sample_vec))

    # Check which case_ids are already in Qdrant (skip re-ingest)
    all_case_ids = list({c["case_id"] for c in chunks})
    existing = store.case_ids_exist(all_case_ids)
    if existing:
        log.info("Skipping %d already-ingested cases", len(existing))
        chunks = [c for c in chunks if c["case_id"] not in existing]
        log.info("%d chunks remaining after dedup", len(chunks))

    if not chunks:
        log.info("All cases already ingested")
        return

    # Embed and upsert in batches
    texts = [c["text"] for c in chunks]
    vectors: list[list[float]] = []

    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i: i + EMBED_BATCH]
        batch_vecs = await embedder.embed_batch(batch, batch_size=EMBED_BATCH)
        vectors.extend(batch_vecs)
        log.info("Embedded %d/%d chunks", min(i + EMBED_BATCH, len(texts)), len(texts))

    for i in range(0, len(chunks), UPSERT_BATCH):
        batch_chunks = chunks[i: i + UPSERT_BATCH]
        batch_vecs = vectors[i: i + UPSERT_BATCH]
        store.upsert(batch_vecs, batch_chunks)
        log.info("Upserted %d/%d chunks", min(i + UPSERT_BATCH, len(chunks)), len(chunks))

    log.info("Done. %d chunks in collection '%s'", len(chunks), collection)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NSW NCAT decisions from AustLII")
    parser.add_argument(
        "--years", nargs="+", type=int, default=[2025, 2026],
        help="Years to scrape (default: 2025 2026)"
    )
    parser.add_argument(
        "--collection", default="nsw_tenancy_ncat",
        help="Qdrant collection name (default: nsw_tenancy_ncat)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scrape only, do not embed or upsert"
    )
    args = parser.parse_args()
    asyncio.run(run(args.years, args.collection, args.dry_run))


if __name__ == "__main__":
    main()
