"""Input sanitization - non-overridable security layer.

Strips control characters and rejects obvious prompt injection patterns
before any jurisdiction-specific processing occurs.
"""

import re
import unicodedata

from fastapi import HTTPException

_INJECTION_RE = re.compile(
    r"ignore\s+(previous|all|prior|above)\s+(instructions?|rules?|prompts?)"
    r"|forget\s+(previous|all|prior|above)\s+(instructions?|rules?|prompts?)"
    r"|you\s+are\s+now\s+(a\s+|an\s+)?"
    r"|act\s+as\s+(if\s+)?(you\s+are\s+)?"
    r"|pretend\s+(you|to\s+be)"
    r"|system\s*prompt\s*:"
    r"|<\s*system\s*>",
    re.IGNORECASE,
)

# Street types used in address-only detection.
_ST = (
    r"st(?:reet)?|rd|road|ave(?:nue)?|dr(?:ive)?"
    r"|lane|pl(?:ace)?|cres(?:cent)?|tce|terrace"
    r"|way|cl(?:ose)?|ct|court"
)

# Matches a string that is essentially just a property address.
# Anchored at both ends so embedded addresses in real questions don't trigger.
_ADDRESS_ONLY_RE = re.compile(
    rf"""
    ^
    (\d+\s+)?                     # optional house number
    (?:[\w'-]{{1,25}}\s+){{1,4}}  # 1-4 street name words
    (?:{_ST})\b                   # street type keyword
    [\s,]*
    (?:[\w][\w\s,]{{0,60}})?      # optional suburb / city / region
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)

# If any of these words appear, the query has legal framing - do not flag.
_LEGAL_TERMS_RE = re.compile(
    r"\b(?:landlord|tenant|bond|rent(?:al)?|notice|lease|damage|repair|"
    r"evict|rights?|tribunal|rta|section|s\d+|claim|dispute|agreement|"
    r"inspection|compensation|termination|fixed.?term|periodic|flat|"
    r"property|house|home|breach|arrear|week|month|pay|owe|liable|"
    r"habitable|healthy.?homes?|deposit|contract)\b",
    re.IGNORECASE,
)


def sanitize_question(text: str, max_chars: int = 1200) -> str:
    """Strip control characters, enforce length, detect prompt injection.

    Raises HTTPException 400 on any violation.
    This function is called by core/api.py before any jurisdiction code runs.
    """
    text = "".join(
        c for c in text
        if unicodedata.category(c) not in ("Cc", "Cf") or c in "\n\t"
    )
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail={"error": "Question must not be empty."})
    if len(text) > max_chars:
        raise HTTPException(
            status_code=400,
            detail={"error": f"Question too long (max {max_chars} characters)."},
        )
    if _INJECTION_RE.search(text):
        raise HTTPException(
            status_code=400,
            detail={"error": "Question contains content that cannot be processed."},
        )
    if (
        len(text) <= 80
        and not _LEGAL_TERMS_RE.search(text)
        and _ADDRESS_ONLY_RE.match(text)
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "error": (
                    "This looks like a property address rather than a legal question. "
                    "Try describing your situation instead - for example: "
                    "'My landlord hasn't fixed the heating at my rental.'"
                )
            },
        )
    return text
