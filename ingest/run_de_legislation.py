"""Ingest German federal traffic law legislation from gesetze-im-internet.de.

Fetches each Paragraph/section page, chunks the text, embeds with the
same model used by the corpus, and upserts into a Qdrant collection.
Idempotent - already-ingested case_ids are skipped.

Usage:
    python ingest/run_de_legislation.py
    python ingest/run_de_legislation.py --laws StVO BKatV
    python ingest/run_de_legislation.py --collection de_legal --dry-run
    python ingest/run_de_legislation.py --list
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Law registry
# ---------------------------------------------------------------------------

LAWS: dict[str, dict] = {
    "StVO": {
        "court_name": "Strassenverkehrs-Ordnung",
        "base_url": "https://www.gesetze-im-internet.de/stvo_2013/",
        "date": "2013-04-26",
    },
    "StVG": {
        "court_name": "Strassenverkehrsgesetz",
        "base_url": "https://www.gesetze-im-internet.de/stvg/",
        "date": "1909-05-03",
    },
    "OWiG": {
        "court_name": "Gesetz ueber Ordnungswidrigkeiten",
        "base_url": "https://www.gesetze-im-internet.de/owig_1968/",
        "date": "1968-01-24",
    },
    "FeV": {
        "court_name": "Fahrerlaubnis-Verordnung",
        "base_url": "https://www.gesetze-im-internet.de/fev_2010/",
        "date": "2010-12-13",
    },
    "BKatV": {
        "court_name": "Bussgeldkatalog-Verordnung",
        "base_url": "https://www.gesetze-im-internet.de/bkatv_2013/",
        "date": "2013-03-14",
    },
}

_NS = uuid.UUID("b2c3d4e5-f6a7-8901-bcde-f12345678901")
COLLECTION = "de_legal"
QDRANT_URL = "http://localhost:6333"
COURT = "DELEG"
REQUEST_DELAY = 0.5     # seconds between HTTP requests - be polite
HEADERS = {
    "User-Agent": "FlensburgBot/1.0 (legal RAG research; +https://github.com/jwongso/flensburg)",
    "Accept-Language": "de-DE,de;q=0.9",
}

# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _get(client: httpx.Client, url: str) -> str:
    resp = client.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return resp.text


def _section_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Parse the law index page and return [(href, label), ...] for each section."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Section links: __1.html, __1a.html, BJNR...html, anlage*.html, anhang*.html
        if not re.search(r"(__[\w]+\.html|BJNR[\w]+\.html|anlage[\w]*\.html|anhang[\w]*\.html)", href):
            continue
        # Normalise to absolute URL
        if href.startswith("http"):
            full = href
        elif href.startswith("/"):
            full = "https://www.gesetze-im-internet.de" + href
        else:
            full = base_url.rstrip("/") + "/" + href
        if full in seen:
            continue
        seen.add(full)
        label = a.get_text(strip=True)
        links.append((full, label))

    return links


def _parse_section(html: str, url: str, act_id: str, law: dict) -> list[dict] | None:
    """Parse a single section page. Returns list of chunk dicts or None if no content."""
    soup = BeautifulSoup(html, "html.parser")

    # Find the section heading - gesetze-im-internet.de uses h2 or div.jnheader
    heading_el = (
        soup.find("div", class_="jnheader")
        or soup.find("h2", class_=re.compile(r"jn"))
        or soup.find("h2")
    )
    heading_text = heading_el.get_text(" ", strip=True) if heading_el else ""

    # Extract the canonical section number from URL
    # __3.html -> s3, __3a.html -> s3a, anlage.html -> anlage, anhang_1.html -> anhang_1
    m = re.search(r"/(__[^/]+|anlage[\w]*|anhang[\w]*)\.html", url)
    raw_num = m.group(1) if m else ""
    if raw_num.startswith("BJNR") or not raw_num:
        return None
    if raw_num.startswith("__"):
        section_num = raw_num.lstrip("_")
    else:
        section_num = raw_num  # anlage, anhang_1, etc.
    case_id = f"DELEG/{act_id}/{section_num}"

    # Extract body text - try multiple selector strategies
    content_div = (
        soup.find("div", id="content")
        or soup.find("div", class_=re.compile(r"jnnorm|jurNorm|norm"))
        or soup.find("article")
        or soup.find("main")
    )
    if content_div is None:
        content_div = soup.body

    if content_div is None:
        return None

    # Remove navigation elements
    for tag in content_div.find_all(["nav", "script", "style", "aside"]):
        tag.decompose()

    text = content_div.get_text(" ", strip=True)
    # Collapse whitespace
    text = re.sub(r"\s{2,}", " ", text).strip()

    if len(text) < 30:
        return None

    title = heading_text or f"{act_id} {section_num}"

    # Chunk into ~150-word windows (law sections are often dense; slightly bigger windows)
    words = text.split()
    chunk_size = 150
    chunks = []
    for i in range(0, len(words), chunk_size):
        window = words[i: i + chunk_size]
        if len(window) < 10:
            continue
        idx = i // chunk_size
        chunks.append({
            "case_id": case_id,
            "chunk_index": idx,
            "court": COURT,
            "court_name": law["court_name"],
            "title": title,
            "date": law["date"],
            "url": url,
            "text": " ".join(window),
            "source_type": "legislation",
        })
    return chunks or None


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------

def _point_id(case_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(_NS, f"{case_id}:{chunk_index}"))


def _ensure_collection(client, collection: str, dim: int) -> None:
    from qdrant_client.models import Distance, VectorParams
    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        print(f"  Created Qdrant collection '{collection}' (dim={dim})")
    else:
        print(f"  Collection '{collection}' already exists")


def _existing_case_ids(client, collection: str) -> set[str]:
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    ids: set[str] = set()
    offset = None
    filt = Filter(must=[FieldCondition(key="court", match=MatchValue(value=COURT))])
    while True:
        result, next_off = client.scroll(
            collection_name=collection,
            scroll_filter=filt,
            limit=200,
            offset=offset,
            with_payload=["case_id"],
        )
        for p in result:
            cid = (p.payload or {}).get("case_id", "")
            if cid:
                ids.add(cid)
        if next_off is None:
            break
        offset = next_off
    return ids


def _upsert(client, collection: str, vectors: list, payloads: list[dict]) -> None:
    from qdrant_client.models import PointStruct
    points = [
        PointStruct(id=_point_id(p["case_id"], p["chunk_index"]), vector=v, payload=p)
        for v, p in zip(vectors, payloads)
    ]
    client.upsert(collection_name=collection, points=points)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(laws: list[str], collection: str, qdrant_url: str, dry_run: bool) -> None:
    from qdrant_client import QdrantClient
    from core.embedder import Embedder

    print("Loading embedder...")
    embedder = Embedder()
    sample = embedder.encode_documents(["test"])[0]
    dim = len(sample)
    print(f"  Model: {embedder._model_name}, dim={dim}")

    qdrant = QdrantClient(url=qdrant_url)

    if not dry_run:
        _ensure_collection(qdrant, collection, dim)
        existing_ids = _existing_case_ids(qdrant, collection)
        print(f"  {len(existing_ids)} legislation chunks already in collection")
    else:
        existing_ids = set()

    with httpx.Client() as http:
        for act_id in laws:
            law = LAWS[act_id]
            print(f"\n=== {act_id} - {law['court_name']} ===")
            print(f"  Fetching index: {law['base_url']}")

            try:
                index_html = _get(http, law["base_url"])
            except Exception as e:
                print(f"  ERROR fetching index: {e}")
                continue

            section_links = _section_links(index_html, law["base_url"])
            print(f"  Found {len(section_links)} section link(s)")

            if not section_links:
                print("  WARNING: no section links found - check HTML structure")
                continue

            law_chunks: list[dict] = []
            for url, label in section_links:
                time.sleep(REQUEST_DELAY)
                try:
                    page_html = _get(http, url)
                except Exception as e:
                    print(f"  ERROR {url}: {e}")
                    continue

                chunks = _parse_section(page_html, url, act_id, law)
                if not chunks:
                    print(f"  SKIP {url} (no parseable content)")
                    continue

                case_id = chunks[0]["case_id"]
                if case_id in existing_ids:
                    print(f"  skip {case_id} (already ingested)")
                    continue

                law_chunks.extend(chunks)
                print(f"  {case_id}: {len(chunks)} chunk(s) | {label}")

            if not law_chunks:
                print(f"  No new chunks for {act_id}")
                continue

            print(f"\n  Embedding {len(law_chunks)} chunks for {act_id}...")
            if dry_run:
                print(f"  --dry-run: skipping embed/upsert")
                for c in law_chunks[:3]:
                    print(f"    {c['case_id']} chunk {c['chunk_index']}: {c['text'][:80]}...")
                continue

            texts = [c["text"] for c in law_chunks]
            vectors = embedder.encode_documents(texts)
            _upsert(qdrant, collection, vectors, law_chunks)
            print(f"  Upserted {len(law_chunks)} chunks for {act_id}")

    print("\nDone.")


def list_ingested(collection: str, qdrant_url: str) -> None:
    from qdrant_client import QdrantClient
    qdrant = QdrantClient(url=qdrant_url)
    ids = _existing_case_ids(qdrant, collection)
    if not ids:
        print("No legislation chunks found.")
        return
    # Group by act
    by_act: dict[str, list[str]] = {}
    for cid in sorted(ids):
        parts = cid.split("/")
        act = parts[1] if len(parts) >= 2 else "?"
        by_act.setdefault(act, []).append(cid)
    for act, cids in sorted(by_act.items()):
        print(f"{act}: {len(cids)} section(s)")
        for cid in cids[:5]:
            print(f"  {cid}")
        if len(cids) > 5:
            print(f"  ... and {len(cids)-5} more")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest German traffic law into Qdrant")
    parser.add_argument(
        "--laws", nargs="+", default=list(LAWS.keys()),
        choices=list(LAWS.keys()),
        help="Which laws to ingest (default: all)",
    )
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--qdrant-url", default=QDRANT_URL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse but do not embed or upsert")
    parser.add_argument("--list", action="store_true",
                        help="List already-ingested sections and exit")
    args = parser.parse_args()

    if args.list:
        list_ingested(args.collection, args.qdrant_url)
        return

    run(args.laws, args.collection, args.qdrant_url, args.dry_run)


if __name__ == "__main__":
    main()
