from __future__ import annotations

import json
import re
import time

import redis.asyncio as aioredis

_SESSION_PREFIX = "astraea:session"
_SESSION_TTL = 7 * 24 * 3600
_SESSION_MAX_TURNS = 10
_SESSION_INJECT_TURNS = 3
_SESSION_ANSWER_CAP = 400
_SESSION_ID_RE = re.compile(r"^[0-9a-f\-]{32,36}$")


async def _load_session(
    redis: "aioredis.Redis | None",
    jurisdiction_name: str,
    session_id: str,
) -> list[dict]:
    """Return the last _SESSION_INJECT_TURNS turns for this session, refreshing TTL."""
    if not redis or not session_id or not _SESSION_ID_RE.match(session_id):
        return []
    key = f"{_SESSION_PREFIX}:{jurisdiction_name}:{session_id}"
    try:
        raw = await redis.get(key)
        if not raw:
            return []
        await redis.expire(key, _SESSION_TTL)
        return json.loads(raw)[-_SESSION_INJECT_TURNS:]
    except Exception:
        return []


async def _save_session(
    redis: "aioredis.Redis | None",
    jurisdiction_name: str,
    session_id: str,
    question: str,
    answer: str,
) -> None:
    """Append a Q&A turn and persist with a sliding 7-day TTL."""
    if not redis or not session_id or not _SESSION_ID_RE.match(session_id):
        return
    key = f"{_SESSION_PREFIX}:{jurisdiction_name}:{session_id}"
    try:
        raw = await redis.get(key)
        turns = json.loads(raw) if raw else []
        turns.append({"q": question, "a": answer[:_SESSION_ANSWER_CAP], "ts": time.time()})
        turns = turns[-_SESSION_MAX_TURNS:]
        await redis.setex(key, _SESSION_TTL, json.dumps(turns))
    except Exception:
        pass


def _format_session_context(turns: list[dict]) -> str:
    if not turns:
        return ""
    lines = ["Recent conversation (use only if directly relevant to the current question):"]
    for t in turns:
        a = t["a"]
        if len(a) >= _SESSION_ANSWER_CAP:
            a += "..."
        lines.append(f"\nQ: {t['q']}\nA: {a}")
    return "\n".join(lines)
