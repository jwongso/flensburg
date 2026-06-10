#!/usr/bin/env python3
"""Self-healing question log analyzer.

Watches a question log file. On each run it compares the last N entries
against a stored state. If nothing has changed, it exits silently. If new
questions arrived, it re-runs each one through /retrieve (with scores) and
asks the LLM whether anything is worth reporting.

The LLM either:
  - Outputs "CLEAR" (nothing to flag) -> state updated, no file written.
  - Outputs a markdown report -> saved to the reports directory with a
    timestamp. You review it and decide what to act on.

Intended to run from cron every 5-10 minutes:
    */5 * * * * cd /home/wdha/proj/priv/nz-legal-rag && \\
        API_URL=http://127.0.0.1:8001 PUBLIC_TOKEN=<token> \\
        python /home/wdha/proj/priv/astraea/scripts/self_analyzer.py \\
        --watch data/question_log.jsonl --out data/reports/

Environment variables:
    API_URL        retrieve endpoint base URL  (default: http://127.0.0.1:8001)
    PUBLIC_TOKEN   API auth token              (default: empty)
    LLM_BASE_URL   LLM endpoint               (default: http://localhost:8080/v1)
    LLM_MODEL      model name                 (default: qwen3)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_API_URL   = os.getenv("API_URL",      "http://127.0.0.1:8001")
_TOKEN     = os.getenv("PUBLIC_TOKEN", "")
_LLM_URL   = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
_LLM_MODEL = os.getenv("LLM_MODEL",    "qwen3")

_RETRIEVE_TIMEOUT = 30
_LLM_TIMEOUT      = 120

# ---------------------------------------------------------------------------
# Log reading
# ---------------------------------------------------------------------------

def _tail(path: Path, n: int) -> list[dict]:
    """Return the last n parsed entries from a JSONL file."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries[-n:]


def _entry_key(e: dict) -> str:
    return f"{e.get('ts', '')}|{e.get('q', '')}"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _load_state(path: Path) -> list[str]:
    """Return the list of entry keys from the last run."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("keys", [])
    except Exception:
        return []


def _save_state(path: Path, keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"keys": keys, "updated": datetime.now(timezone.utc).isoformat()}))


# ---------------------------------------------------------------------------
# Retrieve debug
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
        scores = [s["_score"] for s in sources if "_score" in s]
        top = max(scores) if scores else 0.0
        n = len(scores)
        if top >= 0.82 and n >= 4:
            confidence = "high"
        elif top >= 0.77 and n >= 2:
            confidence = "medium"
        else:
            confidence = "low"
        titles = [s.get("title") or s.get("case_id", "") for s in sources[:3]]
        return {
            "confidence": confidence,
            "source_count": len(sources),
            "top_score": round(top, 4),
            "titles": titles,
            "scores": [round(s, 4) for s in scores],
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# LLM decision
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are a self-healing monitor for a legal RAG system (NZ tenancy law).

You will receive a list of new user questions, each with retrieval debug data \
(confidence level, number of sources, top relevance score, matched source titles).

Your job: decide if any of these questions exposed a problem worth a developer's \
attention.

Problems worth reporting:
- Question got 0 sources (retrieval failure)
- Low confidence AND sources look unrelated to the question
- Question is asking something the system clearly cannot handle well
- Unusual pattern that suggests a gap in routes or prompt coverage

NOT worth reporting:
- Very short queries that got decent related sources anyway
- Questions with medium/high confidence
- Normal legal questions that retrieved plausible content

Output rules:
- If nothing is worth reporting: output exactly the word CLEAR on its own line.
- If something is worth reporting: output a brief markdown report.
  Start with "## Self-Analyzer Report" then list only the issues.
  3-5 lines per issue max. Be specific about what went wrong and one fix suggestion.
  Do not pad. Do not report things that are fine."""


def _ask_llm(entries_with_debug: list[dict]) -> str:
    lines = []
    for i, e in enumerate(entries_with_debug, 1):
        q = e["q"]
        dbg = e["debug"]
        lines.append(f"[{i}] Question: {q!r}")
        if "error" in dbg:
            lines.append(f"    Retrieve error: {dbg['error']}")
        else:
            lines.append(
                f"    Confidence: {dbg['confidence']} | "
                f"Sources: {dbg['source_count']} | "
                f"Top score: {dbg['top_score']}"
            )
            if dbg["titles"]:
                lines.append(f"    Top sources: {' | '.join(dbg['titles'][:3])}")
        lines.append("")

    try:
        r = httpx.post(
            f"{_LLM_URL}/chat/completions",
            json={
                "model": _LLM_MODEL,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": "\n".join(lines)},
                ],
                "max_tokens": 600,
                "temperature": 0.1,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=_LLM_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"## Self-Analyzer Report\n\nLLM call failed: {e}\n\nRaw entries:\n\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch",  required=True, help="Path to question_log.jsonl")
    parser.add_argument("--out",    default="data/reports", help="Directory to write reports")
    parser.add_argument("--state",  default=None, help="State file path (default: <out>/.analyzer_state.json)")
    parser.add_argument("--queue",  type=int, default=3, help="Number of recent entries to track (default: 3)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    watch_path = Path(args.watch)
    out_dir    = Path(args.out)
    state_path = Path(args.state) if args.state else out_dir / ".analyzer_state.json"

    tail = _tail(watch_path, args.queue)
    if not tail:
        if args.verbose:
            print("[self_analyzer] log empty or missing - nothing to do")
        return

    current_keys = [_entry_key(e) for e in tail]
    stored_keys  = _load_state(state_path)

    if current_keys == stored_keys:
        if args.verbose:
            print("[self_analyzer] no new entries - exiting")
        return

    new_entries = [e for e in tail if _entry_key(e) not in set(stored_keys)]
    if args.verbose:
        print(f"[self_analyzer] {len(new_entries)} new entries, running analysis")

    # Retrieve debug for each new entry
    for entry in new_entries:
        if args.verbose:
            print(f"[self_analyzer] retrieve: {entry['q'][:60]!r}")
        entry["debug"] = _retrieve(entry["q"])

    # Ask LLM to decide
    llm_output = _ask_llm(new_entries)

    # Update state regardless of outcome
    _save_state(state_path, current_keys)

    if llm_output.strip().upper().startswith("CLEAR"):
        if args.verbose:
            print("[self_analyzer] LLM: all clear - no report written")
        return

    # Write report
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"report_{ts}.md"
    header = (
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"by self_analyzer | {len(new_entries)} new question(s)_\n\n"
    )
    report_path.write_text(header + llm_output + "\n")
    print(f"[self_analyzer] report written: {report_path}")


if __name__ == "__main__":
    main()
