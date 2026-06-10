#!/usr/bin/env python3
"""Analyze question logs and feedback to surface issues worth reviewing.

For each flagged entry the script:
  1. Re-runs /retrieve to capture confidence, sources, and top scores.
  2. Attaches any stored feedback (rating + comment) from feedback.jsonl.
  3. Sends everything to the local LLM for a brief structured assessment.
  4. Writes a markdown report you can read and decide what to act on.

Flagging heuristics (any one triggers):
  - Negative feedback rating (-1)
  - Query length < 25 chars (vague / test input)
  - /retrieve returned 0 sources (retrieval failure)
  - Confidence level == "low" after refine

Usage:
    python scripts/analyze_logs.py                            # last 7 days
    python scripts/analyze_logs.py --since 1d                 # last 24 hours
    python scripts/analyze_logs.py --since 2026-06-01         # from date (UTC)
    python scripts/analyze_logs.py --out data/reports/        # report directory

Environment variables (all optional):
    API_URL         base URL of the running app  (default: http://127.0.0.1:8081)
    PUBLIC_TOKEN    API auth token               (default: empty)
    LLM_BASE_URL    LLM endpoint                 (default: http://localhost:8080/v1)
    LLM_MODEL       model name                   (default: qwen3)
    LOG_PATH        question_log.jsonl path       (default: data/question_log.jsonl)
    FEEDBACK_PATH   feedback.jsonl path           (default: data/feedback.jsonl)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_API_URL       = os.getenv("API_URL",      "http://127.0.0.1:8001")
_TOKEN         = os.getenv("PUBLIC_TOKEN", "")
_LLM_URL       = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
_LLM_MODEL     = os.getenv("LLM_MODEL",    "qwen3")
_LOG_PATH      = Path(os.getenv("LOG_PATH",      "data/question_log.jsonl"))
_FEEDBACK_PATH = Path(os.getenv("FEEDBACK_PATH", "data/feedback.jsonl"))

_SHORT_QUERY_CHARS = 25
_RETRIEVE_TIMEOUT  = 30


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_since(since: str) -> datetime:
    since = since.strip()
    if since.endswith("d"):
        return datetime.now(timezone.utc) - timedelta(days=int(since[:-1]))
    if since.endswith("h"):
        return datetime.now(timezone.utc) - timedelta(hours=int(since[:-1]))
    return datetime.fromisoformat(since).replace(tzinfo=timezone.utc)


def _load_questions(path: Path, since: datetime) -> list[dict]:
    if not path.exists():
        print(f"[warn] question log not found: {path}", file=sys.stderr)
        return []
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            ts = datetime.fromisoformat(e["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= since:
                entries.append({"ts": ts, "q": e["q"]})
        except Exception:
            continue
    return entries


def _load_feedback(path: Path) -> dict[str, list[dict]]:
    """Return feedback keyed by normalised question text."""
    result: dict[str, list[dict]] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            key = e.get("question", "").strip().lower()
            if key:
                result.setdefault(key, []).append(e)
        except Exception:
            continue
    return result


# ---------------------------------------------------------------------------
# Flagging
# ---------------------------------------------------------------------------

def _flag_reasons(entry: dict, feedback_map: dict[str, list[dict]]) -> list[str]:
    reasons = []
    q = entry["q"]
    if len(q.strip()) < _SHORT_QUERY_CHARS:
        reasons.append("short/vague query")
    fbs = feedback_map.get(q.strip().lower(), [])
    if any(f.get("rating") == -1 for f in fbs):
        reasons.append("negative feedback")
    return reasons


# ---------------------------------------------------------------------------
# Retrieve with debug
# ---------------------------------------------------------------------------

def _retrieve(question: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if _TOKEN:
        headers["X-API-Key"] = _TOKEN
    try:
        r = httpx.post(
            f"{_API_URL}/retrieve",
            json={"question": question},
            headers=headers,
            timeout=_RETRIEVE_TIMEOUT,
        )
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "sources": [], "scores": []}
        data = r.json()
        sources = data.get("sources", [])
        scores = [s.get("_score", 0) for s in sources if "_score" in s]
        # Compute confidence ourselves using the same thresholds as core/api.py
        top = max(scores) if scores else 0
        n = len(scores)
        if top >= 0.82 and n >= 4:
            confidence = "high"
        elif top >= 0.77 and n >= 2:
            confidence = "medium"
        else:
            confidence = "low"
        return {
            "sources": sources,
            "scores": scores,
            "confidence": confidence,
            "top_score": round(top, 4),
            "source_count": len(sources),
        }
    except Exception as e:
        return {"error": str(e), "sources": [], "scores": [], "confidence": "unknown"}


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------

_ANALYSIS_SYSTEM = """\
You are reviewing a legal RAG system's question log to surface issues worth a developer's attention.
For each flagged query you receive: the original question, why it was flagged, \
retrieval debug data (confidence, source count, top relevance score, source titles), \
and any user feedback.

Write a concise markdown report. For each entry:
- State what the user was likely asking
- Assess whether retrieval worked correctly
- Identify any systemic issue (route gap, prompt weakness, vague query, etc.)
- Give one clear recommendation: ignore / add smoke fixture / add route / fix prompt / other

Be direct. Each entry should be 3-6 lines. No padding."""


def _analyze(flagged: list[dict]) -> str:
    if not flagged:
        return "_No flagged entries in the analysis window._\n"

    lines = []
    for i, entry in enumerate(flagged, 1):
        q = entry["q"]
        reasons = ", ".join(entry["reasons"])
        dbg = entry["debug"]
        fbs = entry.get("feedback", [])

        lines.append(f"--- Entry {i} ---")
        lines.append(f"Question: {q!r}")
        lines.append(f"Flagged because: {reasons}")
        lines.append(f"Asked: {entry['ts'].strftime('%Y-%m-%d %H:%M UTC')}")

        if "error" in dbg:
            lines.append(f"Retrieve error: {dbg['error']}")
        else:
            lines.append(
                f"Confidence: {dbg['confidence']} | "
                f"Sources: {dbg['source_count']} | "
                f"Top score: {dbg.get('top_score', '?')}"
            )
            titles = [
                s.get("title") or s.get("case_id", "?")
                for s in dbg["sources"][:3]
            ]
            if titles:
                lines.append("Top sources: " + " | ".join(titles))

        if fbs:
            for fb in fbs:
                rating = "thumbs-up" if fb.get("rating") == 1 else "thumbs-down"
                comment = fb.get("comment", "").strip()
                lines.append(f"Feedback: {rating}" + (f' - "{comment}"' if comment else ""))

        lines.append("")

    prompt = "\n".join(lines)

    try:
        r = httpx.post(
            f"{_LLM_URL}/chat/completions",
            json={
                "model": _LLM_MODEL,
                "messages": [
                    {"role": "system", "content": _ANALYSIS_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 1200,
                "temperature": 0.2,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"_LLM analysis failed: {e}_\n\n**Raw data:**\n\n```\n{prompt}\n```"


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def _build_report(
    flagged: list[dict],
    analysis: str,
    since: datetime,
    total_questions: int,
) -> str:
    now = datetime.now(timezone.utc)
    lines = [
        f"# Question Log Analysis - {now.strftime('%Y-%m-%d')}",
        "",
        f"**Period:** {since.strftime('%Y-%m-%d %H:%M UTC')} to {now.strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Total questions:** {total_questions}  ",
        f"**Flagged for review:** {len(flagged)}  ",
        "",
    ]

    if flagged:
        lines += ["## Flagged Queries (raw)", ""]
        for entry in flagged:
            q = entry["q"]
            reasons = ", ".join(entry["reasons"])
            dbg = entry["debug"]
            conf = dbg.get("confidence", "?")
            n = dbg.get("source_count", "?")
            top = dbg.get("top_score", "?")
            lines.append(
                f"- `{q[:80]}` - **{reasons}** "
                f"(confidence={conf}, sources={n}, top={top})"
            )
        lines += [""]

    lines += [
        "## LLM Assessment",
        "",
        analysis,
        "",
        "---",
        f"_Generated {now.strftime('%Y-%m-%d %H:%M UTC')} by scripts/analyze_logs.py_",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze question logs and feedback.")
    parser.add_argument("--since", default="7d",
                        help="Time window: Nd, Nh, or YYYY-MM-DD (default: 7d)")
    parser.add_argument("--out", default=None,
                        help="Directory to write report to (default: print to stdout)")
    parser.add_argument("--log",      default=str(_LOG_PATH))
    parser.add_argument("--feedback", default=str(_FEEDBACK_PATH))
    args = parser.parse_args()

    since = _parse_since(args.since)
    print(f"[analyze] window: {since.strftime('%Y-%m-%d %H:%M UTC')} -> now", file=sys.stderr)

    questions = _load_questions(Path(args.log), since)
    print(f"[analyze] {len(questions)} questions in window", file=sys.stderr)

    feedback_map = _load_feedback(Path(args.feedback))

    # Flag interesting entries
    flagged: list[dict] = []
    seen: set[str] = set()
    for entry in questions:
        q = entry["q"]
        if q in seen:
            continue
        seen.add(q)
        reasons = _flag_reasons(entry, feedback_map)
        if reasons:
            flagged.append({**entry, "reasons": reasons, "feedback": feedback_map.get(q.strip().lower(), [])})

    print(f"[analyze] {len(flagged)} unique flagged entries", file=sys.stderr)

    # Re-run retrieve for each flagged entry
    for entry in flagged:
        print(f"[analyze] retrieving: {entry['q'][:60]!r}", file=sys.stderr)
        dbg = _retrieve(entry["q"])
        entry["debug"] = dbg
        # Also flag if retrieval returned nothing even if not already flagged
        if dbg.get("source_count", 1) == 0 and "retrieval failure" not in entry["reasons"]:
            entry["reasons"].append("retrieval failure")
        if dbg.get("confidence") == "low" and "low confidence" not in entry["reasons"]:
            entry["reasons"].append("low confidence")

    # LLM analysis
    print("[analyze] running LLM analysis...", file=sys.stderr)
    analysis = _analyze(flagged)

    report = _build_report(flagged, analysis, since, len(questions))

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"analysis_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.md"
        out_path = out_dir / fname
        out_path.write_text(report)
        print(f"[analyze] report written to {out_path}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
