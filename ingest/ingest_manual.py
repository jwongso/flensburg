"""Manual ingestion of secondary sources (PDF, DOCX, TXT, MD) into a Qdrant collection.

Extracts text, chunks into ~120-word windows, embeds with the same model used by
the corpus, and upserts to the target collection. Idempotent - running twice on
the same file is a no-op (chunks are deduplicated by document slug + chunk index).

Usage:
    python -m ingest.ingest_manual path/to/paper.pdf
    python -m ingest.ingest_manual *.pdf --collection nztt_moj --source-type guidance
    python -m ingest.ingest_manual paper.pdf --title "Housing Law Review 2024" --date 2024-01-01
    python -m ingest.ingest_manual paper.pdf --url https://example.com/paper.pdf --author "J Smith"
    python -m ingest.ingest_manual --list-ingested --collection nztt_moj
    python -m ingest.ingest_manual --delete MANUAL/housing-law-review-2024 --collection nztt_moj
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
import uuid
from pathlib import Path

_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

CHUNK_WORDS = 120
MIN_CHUNK_WORDS = 20
MANUAL_COURT = "MANUAL"
MANUAL_COURT_NAME = "Manual Ingestion"


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_pdf(path: Path) -> tuple[str, int]:
    import fitz
    doc = fitz.open(str(path))
    pages = [page.get_text() for page in doc]
    page_count = len(pages)
    doc.close()
    text = "\n\n".join(pages).strip()

    # OCR fallback for scanned/image-only PDFs
    avg_chars = len(text) / max(page_count, 1)
    if avg_chars < 200:
        print(f"  Low text density ({avg_chars:.0f} chars/page) - attempting OCR...")
        try:
            import pytesseract
            from PIL import Image
            import io
            doc2 = fitz.open(str(path))
            ocr_pages = []
            for page in doc2:
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                ocr_pages.append(pytesseract.image_to_string(img, lang="eng"))
            doc2.close()
            text = "\n\n".join(ocr_pages).strip()
            print(f"  OCR complete: {len(text)} chars")
        except ImportError:
            print("  WARNING: pytesseract/Pillow not installed, skipping OCR")

    return text, page_count


def _extract_docx(path: Path) -> tuple[str, int]:
    from docx import Document
    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs), 0


_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "form"}
_CONTENT_IDS = {"main-content", "main", "content", "page-content", "primary"}
_CONTENT_CLASSES = {"main-content", "page-content", "content-area", "entry-content", "article-body"}


def _extract_url(url: str) -> tuple[str, str]:
    """Fetch a URL and return (text, page_title)."""
    import requests
    from bs4 import BeautifulSoup, Comment

    headers = {"User-Agent": "Mozilla/5.0 (compatible; AstraeaBot/1.0; +https://astraea.localrun.ai)"}
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 404:
        print(f"  WARNING: 404 Not Found - {url}")
        return "", ""
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Page title - normalize whitespace then strip site suffix
    title_tag = soup.find("title")
    page_title = re.sub(r"\s+", " ", title_tag.get_text()).strip() if title_tag else ""
    for sep in [" | ", " - ", " - NZ", " :: ", " » "]:
        if sep in page_title:
            page_title = page_title.split(sep)[0].strip()
            break
    # Also strip trailing " »" without space
    page_title = page_title.rstrip(" »").strip()

    # Remove unwanted tags
    for tag in soup.find_all(_SKIP_TAGS):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Try to find main content region
    main = None
    for id_ in _CONTENT_IDS:
        main = soup.find(id=id_) or soup.find(class_=id_)
        if main:
            break
    if not main:
        for cls in _CONTENT_CLASSES:
            main = soup.find(class_=cls)
            if main:
                break
    if not main:
        main = soup.find("main") or soup.find("article") or soup.body

    if main is None:
        return "", page_title

    # Extract readable text preserving block structure
    lines = []
    for el in main.descendants:
        if el.name in ("h1", "h2", "h3", "h4"):
            text = el.get_text(" ", strip=True)
            if text:
                lines.append(f"\n{text}\n")
        elif el.name in ("p", "li", "td", "dt", "dd"):
            text = el.get_text(" ", strip=True)
            if text:
                lines.append(text)
    text = "\n".join(lines).strip()

    # Fallback: plain text of whole main region
    if len(text) < 200:
        text = main.get_text(" ", strip=True)

    return text, page_title


def _extract_text(path_or_url: "Path | str") -> tuple[str, int]:
    s = str(path_or_url)
    if s.startswith("http://") or s.startswith("https://"):
        text, _ = _extract_url(s)
        return text, 0
    path = Path(s)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in (".docx", ".doc"):
        return _extract_docx(path)
    if suffix in (".txt", ".md", ".rst"):
        return path.read_text(encoding="utf-8", errors="replace"), 0
    raise ValueError(f"Unsupported file type: {suffix}")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Convert a string to a lowercase ASCII slug."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")[:80]


def _guess_title(text: str, stem: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 10:
            return line[:200]
    return stem


def _guess_year(text: str) -> str:
    m = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", text[:2000])
    return f"{m.group()}-01-01" if m else "2000-01-01"


def chunk_text(
    text: str,
    case_id: str,
    title: str,
    date: str,
    url: str,
    source_type: str,
    author: str,
) -> list[dict]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), CHUNK_WORDS):
        window = words[i: i + CHUNK_WORDS]
        if len(window) < MIN_CHUNK_WORDS:
            continue
        idx = i // CHUNK_WORDS
        chunks.append({
            "case_id": case_id,
            "chunk_index": idx,
            "court": MANUAL_COURT,
            "court_name": MANUAL_COURT_NAME,
            "title": title,
            "date": date,
            "url": url,
            "text": " ".join(window),
            "source_type": source_type,
            "author": author,
        })
    return chunks


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------

def _point_id(case_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(_NS, f"{case_id}:{chunk_index}"))


def _get_client(qdrant_url: str):
    from qdrant_client import QdrantClient
    return QdrantClient(url=qdrant_url)


def _existing_case_ids(client, collection: str) -> set[str]:
    """Scroll through MANUAL court points and collect case_ids."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    case_ids: set[str] = set()
    offset = None
    filt = Filter(must=[FieldCondition(key="court", match=MatchValue(value=MANUAL_COURT))])
    while True:
        result, next_offset = client.scroll(
            collection_name=collection,
            scroll_filter=filt,
            limit=100,
            offset=offset,
            with_payload=["case_id"],
        )
        for point in result:
            cid = (point.payload or {}).get("case_id", "")
            if cid:
                case_ids.add(cid)
        if next_offset is None:
            break
        offset = next_offset
    return case_ids


def _delete_by_case_id(client, collection: str, case_id: str) -> int:
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    result = client.delete(
        collection_name=collection,
        points_selector=Filter(
            must=[FieldCondition(key="case_id", match=MatchValue(value=case_id))]
        ),
    )
    return getattr(result, "deleted", 0)


def _upsert_chunks(client, collection: str, vectors: list, payloads: list[dict]) -> None:
    from qdrant_client.models import PointStruct
    points = [
        PointStruct(
            id=_point_id(p["case_id"], p["chunk_index"]),
            vector=v,
            payload=p,
        )
        for v, p in zip(vectors, payloads)
    ]
    client.upsert(collection_name=collection, points=points)


# ---------------------------------------------------------------------------
# Main ingestion flow
# ---------------------------------------------------------------------------

def ingest_file(
    path: "Path | str",
    client,
    collection: str,
    embedder,
    existing_ids: set[str],
    title: str | None,
    date: str | None,
    url: str | None,
    source_type: str,
    author: str,
    force: bool,
) -> int:
    is_url = str(path).startswith("http://") or str(path).startswith("https://")
    label = str(path) if is_url else Path(path).name
    print(f"\n[{label}]")

    if is_url:
        fetched_text, fetched_title = _extract_url(str(path))
        text, page_count = fetched_text, 0
        # For URLs the canonical URL is the path itself unless overridden
        default_url = str(path)
        default_title = fetched_title or str(path)
    else:
        text, page_count = _extract_text(path)
        default_url = f"file://{Path(path).resolve()}"
        default_title = None

    if not text.strip():
        print("  WARNING: no text extracted, skipping")
        return 0
    print(f"  Extracted {len(text)} chars" + (f" from {page_count} pages" if page_count else ""))

    resolved_title = title or (default_title if is_url else _guess_title(text, Path(path).stem))
    resolved_date = date or _guess_year(text)
    resolved_url = url or default_url

    case_id = f"MANUAL/{_slug(resolved_title)}"
    if case_id in existing_ids and not force:
        print(f"  Already ingested (case_id={case_id}) - skipping. Use --force to re-ingest.")
        return 0

    chunks = chunk_text(text, case_id, resolved_title, resolved_date, resolved_url, source_type, author)
    if not chunks:
        print("  WARNING: no chunks produced, skipping")
        return 0

    print(f"  Title   : {resolved_title[:70]}")
    print(f"  Date    : {resolved_date}")
    print(f"  URL     : {resolved_url[:70]}")
    print(f"  case_id : {case_id}")
    print(f"  Chunks  : {len(chunks)}")

    texts = [c["text"] for c in chunks]
    print(f"  Embedding {len(texts)} chunks...")
    vectors = embedder.encode_documents(texts)

    # Delete old version if re-ingesting
    if case_id in existing_ids and force:
        _delete_by_case_id(client, collection, case_id)
        print(f"  Deleted previous version")

    _upsert_chunks(client, collection, vectors, chunks)
    print(f"  Upserted {len(chunks)} chunks to '{collection}'")
    return len(chunks)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest PDF/DOCX/TXT into Qdrant")
    parser.add_argument("files", nargs="*", help="Files or URLs to ingest")
    parser.add_argument("--collection", default="nztt_moj", help="Qdrant collection name")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--source-type", default="official_policy",
                        choices=[
                            "legislation",           # RTA, regulations (actual statute text)
                            "case_law",              # Tenancy Tribunal / court decisions
                            "official_policy",       # HUD RIS, MoJ reform policy documents
                            "official_guidance",     # Tenancy Services, OPC consumer guidance
                            "law_review",            # VUWLR, Auckland ULR, academic papers
                            "advocacy_submission",   # CAB, NGO submissions
                            "community_legal_guidance", # CAB articles, community legal centres
                            "commercial_commentary", # law firm / property manager articles
                        ])
    parser.add_argument("--title", help="Override document title")
    parser.add_argument("--date", help="Publication date (YYYY-MM-DD)")
    parser.add_argument("--url", help="Canonical URL for the document")
    parser.add_argument("--author", default="", help="Author(s) string")
    parser.add_argument("--device", default=None, choices=["cpu", "cuda"],
                        help="Embedding device (default: auto-select)")
    parser.add_argument("--force", action="store_true", help="Re-ingest even if already present")
    parser.add_argument("--list-ingested", action="store_true",
                        help="List all manually ingested documents and exit")
    parser.add_argument("--delete", metavar="CASE_ID",
                        help="Delete a manually ingested document by case_id and exit")
    args = parser.parse_args()

    client = _get_client(args.qdrant_url)

    if args.list_ingested:
        ids = _existing_case_ids(client, args.collection)
        if not ids:
            print("No manually ingested documents found.")
        else:
            print(f"{len(ids)} manually ingested document(s):")
            for cid in sorted(ids):
                print(f"  {cid}")
        return

    if args.delete:
        _delete_by_case_id(client, args.collection, args.delete)
        print(f"Deleted: {args.delete}")
        return

    if not args.files:
        parser.error("Provide at least one file to ingest, or use --list-ingested / --delete")

    print(f"Loading embedder...")
    from core.embedder import Embedder
    embedder = Embedder(device=args.device)
    print(f"  Model: {embedder._model_name}, dim={embedder.dim}")

    existing_ids = _existing_case_ids(client, args.collection)
    print(f"  {len(existing_ids)} manual document(s) already in collection")

    total_chunks = 0
    for item in args.files:
        is_url = str(item).startswith("http://") or str(item).startswith("https://")
        if not is_url and not Path(item).exists():
            print(f"  ERROR: {item} not found, skipping")
            continue
        total_chunks += ingest_file(
            path=item,
            client=client,
            collection=args.collection,
            embedder=embedder,
            existing_ids=existing_ids,
            title=args.title if len(args.files) == 1 else None,
            date=args.date if len(args.files) == 1 else None,
            url=args.url if len(args.files) == 1 else None,
            source_type=args.source_type,
            author=args.author,
            force=args.force,
        )

    print(f"\nDone. Total chunks upserted: {total_chunks}")


if __name__ == "__main__":
    main()
