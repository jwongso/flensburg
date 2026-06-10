"""Semaphore-based request queue with per-IP fairness.

Non-overridable by jurisdictions: queue concurrency and per-IP enforcement
are security properties of the framework, not jurisdiction config.

Global LLM semaphore (opt-in):
  Set LLM_GLOBAL_CONCURRENCY=N (N >= 1) to limit concurrent LLM generation
  calls across ALL app processes sharing the same Redis instance. This prevents
  a second app from silently stacking requests on a --parallel 1 LLM server.
  Set LLM_GLOBAL_CONCURRENCY=0 (default) to disable and let the LLM handle it.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import HTTPException, Request

_MAX_CONCURRENT = 1
_MAX_QUEUE = 5       # refuse new requests when this many are already waiting
_MAX_WAIT = 60.0
_AVG_QUERY_SECONDS = 25

_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
_active: int = 0
_waiting: int = 0
_ip_in_flight: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Global LLM semaphore (Redis-backed, cross-process)
# ---------------------------------------------------------------------------

LLM_GLOBAL_CONCURRENCY = int(os.getenv("LLM_GLOBAL_CONCURRENCY", "0"))
_GLOBAL_KEY = "astraea:llm:active"
_GLOBAL_TTL = 180           # failsafe TTL - auto-releases if process crashes
_GLOBAL_POLL = 0.5          # seconds between Redis poll attempts

# Atomic Lua: increment counter if below limit, return new value; else -1
_LUA_ACQUIRE = """
local cur = tonumber(redis.call('get', KEYS[1]) or '0')
if cur < tonumber(ARGV[1]) then
    local v = redis.call('incr', KEYS[1])
    redis.call('expire', KEYS[1], tonumber(ARGV[2]))
    return v
end
return -1
"""


async def global_llm_will_wait(redis) -> bool:
    """True if a global LLM slot is currently unavailable."""
    if not LLM_GLOBAL_CONCURRENCY or redis is None:
        return False
    try:
        cur = int(await redis.get(_GLOBAL_KEY) or 0)
        return cur >= LLM_GLOBAL_CONCURRENCY
    except Exception:
        return False


async def global_llm_acquire(redis, timeout: float = 90.0) -> bool:
    """Acquire a global LLM slot. Returns False only on timeout or Redis error.

    When LLM_GLOBAL_CONCURRENCY=0 (disabled), returns True immediately so
    the LLM server handles concurrency on its own (e.g. --parallel N).
    """
    if not LLM_GLOBAL_CONCURRENCY or redis is None:
        return True
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            result = await redis.eval(
                _LUA_ACQUIRE, 1, _GLOBAL_KEY,
                LLM_GLOBAL_CONCURRENCY, _GLOBAL_TTL,
            )
            if result != -1:
                return True
        except Exception:
            return True  # Redis error: fail open so LLM handles it
        await asyncio.sleep(_GLOBAL_POLL)
    return False


async def global_llm_release(redis) -> None:
    """Release a global LLM slot."""
    if not LLM_GLOBAL_CONCURRENCY or redis is None:
        return
    try:
        val = await redis.decr(_GLOBAL_KEY)
        if val <= 0:
            await redis.delete(_GLOBAL_KEY)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Per-process per-IP queue (unchanged)
# ---------------------------------------------------------------------------

def get_client_ip(request: Request) -> str:
    cf = request.headers.get("CF-Connecting-IP")
    if cf:
        return cf
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host or "unknown"


def queue_status() -> dict:
    return {
        "active": _active,
        "waiting": _waiting,
        "max_queue": _MAX_QUEUE,
        "estimated_wait_seconds": max(0, (_waiting * _AVG_QUERY_SECONDS) // max(1, _MAX_CONCURRENT)),
    }


def will_wait() -> bool:
    return _active >= _MAX_CONCURRENT


def queue_wait_estimate() -> dict:
    position = _waiting + 1
    estimated = (position * _AVG_QUERY_SECONDS) // max(1, _MAX_CONCURRENT)
    return {"position": position, "max_queue": _MAX_QUEUE, "active": _active, "estimated_wait_s": estimated}


async def acquire(request: Request) -> str:
    global _active, _waiting
    ip = get_client_ip(request)

    if _ip_in_flight.get(ip, 0) >= 1:
        raise HTTPException(
            status_code=429,
            detail={"error": "You already have a query in progress. Please wait for it to finish.", "retry_after": 30},
        )

    if _waiting >= _MAX_QUEUE:
        raise HTTPException(
            status_code=503,
            detail={"error": "The server is at capacity. Please try again in a moment.", "retry_after": 60},
        )

    _waiting += 1
    _ip_in_flight[ip] = _ip_in_flight.get(ip, 0) + 1

    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=_MAX_WAIT)
    except asyncio.TimeoutError:
        _waiting -= 1
        count = _ip_in_flight.get(ip, 1) - 1
        if count <= 0:
            _ip_in_flight.pop(ip, None)
        else:
            _ip_in_flight[ip] = count
        raise HTTPException(
            status_code=503,
            detail={"error": "The server is busy right now. Please try again in a moment.", "retry_after": 30},
        )

    _waiting -= 1
    _active += 1
    return ip


def release(ip: str) -> None:
    global _active
    _semaphore.release()
    _active -= 1
    count = _ip_in_flight.get(ip, 1) - 1
    if count <= 0:
        _ip_in_flight.pop(ip, None)
    else:
        _ip_in_flight[ip] = count
