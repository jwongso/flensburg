"""Live legislation fetcher and anchor builder.

Fetches full Act page text via headless browser, caches in memory (TTL from
jurisdiction.legislation.cache_ttl_seconds), and extracts section excerpts for
use as legislation anchors in LLM context.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.browser import BrowserSession
    from core.jurisdiction import JurisdictionBase

_NEXT_SECTION_RE = re.compile(r"(?m)^\s*(?:\d+[A-Z]*\s+[A-Z]|Schedule\b|---)")


def extract_section_default(full_text: str, section_num: str) -> str | None:
    """Extract a named section from legislation page text.

    Uses heading-aware matching to discriminate real section headings from
    penalty table rows. Stops at the next heading or schedule boundary.
    """
    heading_re = re.compile(rf"(?m)^\s*{re.escape(section_num)}\s+[A-Z][^\n]{{3,}}")
    for m in heading_re.finditer(full_text):
        candidate = full_text[m.start(): m.start() + 2500]
        nxt = _NEXT_SECTION_RE.search(candidate, 10)
        if nxt:
            candidate = candidate[:nxt.start()]
        if re.search(r"\(\d+\)", candidate):
            return candidate[:1800].strip()
    return None


class LegislationCache:
    """Per-app cache of fetched legislation text, keyed by act_id."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}  # act_id -> (text, ts)

    def get(self, act_id: str, ttl: int) -> str | None:
        entry = self._cache.get(act_id)
        if entry and time.monotonic() - entry[1] < ttl:
            return entry[0]
        return None

    def set(self, act_id: str, text: str) -> None:
        self._cache[act_id] = (text, time.monotonic())

    async def warm(
        self,
        jurisdiction: "JurisdictionBase",
        browser: "BrowserSession",
    ) -> None:
        if not jurisdiction.legislation:
            return
        ttl = jurisdiction.legislation.cache_ttl_seconds
        for act_id, url in jurisdiction.legislation.acts.items():
            if self.get(act_id, ttl):
                continue
            try:
                text = await asyncio.wait_for(
                    browser.fetch_text(url, wait="networkidle"),
                    timeout=20,
                )
                self.set(act_id, text)
            except Exception:
                pass

    def extract_section(
        self,
        act_id: str,
        section: str,
        full_text: str,
        jurisdiction: "JurisdictionBase",
    ) -> str | None:
        result = jurisdiction.extract_section(act_id, section, full_text)
        if result is not None:
            return result
        num = re.sub(r"^[sS]", "", section)
        return extract_section_default(full_text, num)

    def build_anchor(
        self,
        act_id: str,
        full_text: str,
        leg_sources: list[dict],
        jurisdiction: "JurisdictionBase",
    ) -> str:
        """Build a legislation anchor block from live Act text."""
        section_refs: list[str] = []
        seen: set[str] = set()
        for s in leg_sources:
            m = re.search(r"/s?(\d+[A-Z]?)$", s.get("case_id", ""), re.IGNORECASE)
            if m:
                key = m.group(1).upper()
                if key not in seen:
                    seen.add(key)
                    section_refs.append(m.group(1))

        if not section_refs:
            return ""

        lines = [
            "Relevant Act sections "
            "(current live text - use for grounding section numbers only, "
            "do not cite with [SN] notation):"
        ]
        for ref in section_refs[:3]:
            excerpt = self.extract_section(act_id, ref, full_text, jurisdiction)
            if excerpt:
                num = re.sub(r"^[sS]", "", ref)
                lines.append(f"\ns{num} {excerpt}")

        return "\n".join(lines) if len(lines) > 1 else ""


def extract_section_refs(text: str) -> list[str]:
    """Return unique s[0-9]+ refs from text, preserving first-seen order."""
    found = re.findall(r"\bs(\d+[A-Z]?)\b", text)
    seen: set[str] = set()
    result = []
    for ref in found:
        key = ref.upper()
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result
