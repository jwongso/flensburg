from __future__ import annotations

import asyncio
import json
import logging
import re

import redis.asyncio as aioredis

from core.browser import BrowserSession
from core.jurisdiction import JurisdictionBase
from core.legislation import LegislationCache, extract_section_refs

_WEB_CACHE_PREFIX = "astraea:web_verify:"


def _web_cache_key(leg_sources: list[dict], fallback: str, prefix: str) -> str:
    ids = sorted({s.get("case_id", "") for s in leg_sources if s.get("case_id")})
    slug = "|".join(ids) if ids else fallback[:80].lower().strip()
    return f"{prefix}{slug}"


async def _web_verify(
    question: str,
    leg_sources: list[dict],
    browser: BrowserSession,
    redis: aioredis.Redis | None,
    jurisdiction: JurisdictionBase,
    alwaysonline: bool = False,
) -> tuple[str, list[dict], bool]:
    if not jurisdiction.web_verify or browser is None:
        return "", [], False

    wv = jurisdiction.web_verify
    cache_key = _web_cache_key(leg_sources, question, _WEB_CACHE_PREFIX + jurisdiction.name + ":")

    if not alwaysonline and redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                payload = json.loads(cached)
                return payload["text"], payload["results"], True
        except Exception:
            pass

    query = f"{wv.search_prefix} {question[:120]}"
    try:
        results = await asyncio.wait_for(
            browser.search_ddg(query, max_results=wv.max_results),
            timeout=20,
        )
    except Exception as exc:
        logging.warning("_web_verify failed: %s", exc)
        return "", [], False

    if not results:
        return "", [], False

    lines = ["Current online sources (use to verify recent law changes):"]
    for r in results:
        lines.append(f"- {r['title']} | {r['url']}\n  {r['body']}")
    text = "\n".join(lines)

    if redis is not None:
        try:
            payload = json.dumps({"text": text, "results": results, "query": query})
            await redis.setex(cache_key, wv.cache_ttl_seconds, payload)
        except Exception:
            pass

    return text, results, False


async def _verify_sections(
    answer: str,
    leg_sources: list[dict],
    leg_cache: LegislationCache,
    jurisdiction: JurisdictionBase,
) -> list[dict]:
    if not jurisdiction.legislation:
        return []

    leg_refs: list[str] = []
    seen: set[str] = set()
    for s in leg_sources:
        m = re.search(r"/s?(\d+[A-Z]?)$", s.get("case_id", ""), re.IGNORECASE)
        if m:
            key = m.group(1).upper()
            if key not in seen:
                seen.add(key)
                leg_refs.append(m.group(1))
    for ref in extract_section_refs(answer):
        if ref.upper() not in seen:
            seen.add(ref.upper())
            leg_refs.append(ref)

    first_act_id = next(iter(jurisdiction.legislation.acts), None)
    if not first_act_id:
        return []
    first_act_url = jurisdiction.legislation.acts[first_act_id]

    full_text = leg_cache.get(first_act_id, jurisdiction.legislation.cache_ttl_seconds)
    if not full_text:
        return []

    results = []
    for ref in leg_refs[:4]:
        excerpt = leg_cache.extract_section(first_act_id, ref, full_text, jurisdiction)
        if excerpt:
            results.append({
                "reference": f"s{re.sub(r'^[sS]', '', ref)}",
                "excerpt": excerpt,
                "url": first_act_url,
            })
    return results
