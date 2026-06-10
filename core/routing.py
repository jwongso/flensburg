"""Statute routing primitives shared across all jurisdictions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StatuteRoute:
    """Maps a class of user question to the legislation sections that embeddings frequently miss.

    Required:
        intent          - machine-readable label for this route
        include_any     - any of these terms in the combined query triggers the route
        forced_sections - legislation chunk IDs to prepend to vector results
        synthetic_query - embedded to locate forced_sections in the leg collection

    Optional:
        include_all     - ALL of these must also match
        exclude_any     - if any match, skip this route entirely
        leg_allow_list  - when set, only these sections are allowed as legislation anchors;
                          when multiple routes fire, allow-lists are unioned (additive)
        priority        - controls dominant_route for debug display and synthetic query
                          ordering; does not control which allow-list wins (they are unioned)
        notes           - human-readable explanation
    """
    intent: str
    include_any: tuple[str, ...]
    forced_sections: tuple[str, ...]
    synthetic_query: str
    include_all: tuple[str, ...] = ()
    exclude_any: tuple[str, ...] = ()
    leg_allow_list: tuple[str, ...] = ()
    priority: int = 0
    notes: str = ""
    guidance_sources: tuple[str, ...] = ()  # MANUAL case_ids to inject when this route fires; first scoring hit wins
    case_synthetic_query: str = ""  # if set, a supplementary case retrieval pass runs with this query

    # Two-tier trigger matching - use instead of include_any for routes with
    # broad terms that collide in adjacent query contexts.
    #
    # include_any_precise: fires unconditionally - terms unambiguous on their own
    # include_any_broad:   fires only when require_context_any also matches
    # require_context_any: context gate for broad terms; ignored when a precise
    #                      term matched; should be multi-word phrases or highly
    #                      specific single words (not "tenant", "landlord")
    #
    # When any of these are non-empty, include_any is ignored for matching.
    # Routes without collisions keep include_any and need no changes.
    include_any_precise: tuple[str, ...] = ()
    include_any_broad: tuple[str, ...] = ()
    require_context_any: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteDecision:
    """Fully computed routing decision. Callers never inspect raw routes directly.

    Build with build_route_decision(). All fields are derived in one place so
    anchor.py and api.py receive a flat, consistent object.
    """
    triggered: bool
    matched_intents: tuple[str, ...]           # intents of matched routes, for debug/logging
    trigger_terms: tuple[str, ...]             # terms that actually matched in the query
    trigger_paths: tuple[tuple[str, str], ...] # ((intent, "precise"|"broad+context"|"legacy"), ...) per matched route
    forced_sections: tuple[str, ...]           # union of all forced sections, order preserved
    leg_allow_list: tuple[str, ...]            # union of all allow-lists from fired routes (order preserved, deduped)
    boosted_act_ids: frozenset[str]            # act IDs derived from forced_sections, for federated search
    leg_synthetic_queries: tuple[str, ...]     # for legislation injection pass in anchor.py
    case_synthetic_queries: tuple[str, ...]    # for supplementary case retrieval in anchor.py
    dominant_route: str                        # intent of route that owns leg_allow_list; "" if none
    dominance_reason: str                      # human-readable explanation for debug output
    ignored_routes: tuple[tuple[str, str], ...] # ((intent, reason), ...) for debug output
    near_miss_routes: tuple[tuple[str, tuple[str, ...]], ...] # ((intent, (broad_terms_matched, ...)), ...) - broad fired, context gate failed


# Maps curly quotes and dashes to ASCII equivalents before trigger matching.
# Keys are ordinals to avoid source-file encoding issues with literal Unicode.
_NORMALIZE_TABLE = str.maketrans({
    0x2018: 0x27,  # left single quotation mark  -> apostrophe
    0x2019: 0x27,  # right single quotation mark -> apostrophe
    0x201C: 0x22,  # left double quotation mark  -> double quote
    0x201D: 0x22,  # right double quotation mark -> double quote
    0x2014: 0x20,  # em dash                     -> space
    0x2013: 0x20,  # en dash                     -> space
    0x2012: 0x20,  # figure dash                 -> space
    0x2015: 0x20,  # horizontal bar              -> space
    0x002D: 0x20,  # hyphen-minus                -> space
})


def normalize_query(text: str) -> str:
    return " ".join(text.lower().translate(_NORMALIZE_TABLE).split())


def _route_triggered(route: StatuteRoute, q: str) -> bool:
    """Return True if the route's trigger terms match the normalized query.

    Two-tier mode (when include_any_precise or include_any_broad is set):
      - Precise terms fire unconditionally.
      - Broad terms fire only when at least one require_context_any term also
        appears in the query.
    Legacy mode (include_any only): original flat-list behavior.
    """
    if route.include_any_precise or route.include_any_broad:
        if any(t in q for t in route.include_any_precise):
            return True
        if any(t in q for t in route.include_any_broad):
            return any(t in q for t in route.require_context_any)
        return False
    return any(t in q for t in route.include_any)


def _match_routes(q: str, routes: list[StatuteRoute]) -> list[StatuteRoute]:
    """Match a normalized combined query string against the route table."""
    matches: list[StatuteRoute] = []
    for route in routes:
        if route.exclude_any and any(term in q for term in route.exclude_any):
            continue
        if not _route_triggered(route, q):
            continue
        if route.include_all and not all(t in q for t in route.include_all):
            continue
        matches.append(route)
    return matches


def build_route_decision(
    original: str,
    rewritten: str,
    routes: list[StatuteRoute],
) -> RouteDecision:
    """Single public entry point for all routing logic.

    Computes every derived field in one place. anchor.py and api.py call this
    once and use the returned RouteDecision - they never inspect raw routes.
    """
    q = normalize_query(original + " " + rewritten)
    matched = _match_routes(q, routes)

    # Forced sections: union, order preserved (first route wins on duplicates)
    seen_sections: set[str] = set()
    forced: list[str] = []
    for r in matched:
        for s in r.forced_sections:
            if s not in seen_sections:
                forced.append(s)
                seen_sections.add(s)

    # Act IDs for federated search boost
    boosted: set[str] = set()
    for s in forced:
        parts = s.split("/")
        if len(parts) >= 2:
            boosted.add(parts[1])

    # Allow-list: union of all fired routes that define one (order preserved, deduped).
    # dominant is still tracked by priority for debug/display only - it no longer
    # controls which allow-list wins, because leg_allow_list is a filter not a ranking
    # preference. Multi-issue queries need all matched route sections to be accessible.
    allow_candidates = [r for r in matched if r.leg_allow_list]
    if allow_candidates:
        dominant = max(allow_candidates, key=lambda r: r.priority)
        leg_allow_list = tuple(dict.fromkeys(
            s for r in allow_candidates for s in r.leg_allow_list
        ))
    else:
        dominant = max(matched, key=lambda r: r.priority) if matched else None
        leg_allow_list = ()

    # Synthetic queries: deduplicated, order preserved
    leg_synths = list(dict.fromkeys(r.synthetic_query for r in matched if r.synthetic_query))
    case_synths = list(dict.fromkeys(r.case_synthetic_query for r in matched if r.case_synthetic_query))

    # Trigger terms: only the terms that actually appeared in the query.
    # Two-tier routes have include_any=() so must collect from the right fields.
    trigger_term_set: set[str] = set()
    trigger_paths_list: list[tuple[str, str]] = []
    for r in matched:
        if r.include_any_precise or r.include_any_broad:
            if any(t in q for t in r.include_any_precise):
                path = "precise"
                trigger_term_set.update(t for t in r.include_any_precise if t in q)
            else:
                path = "broad+context"
                trigger_term_set.update(t for t in r.include_any_broad if t in q)
                trigger_term_set.update(t for t in r.require_context_any if t in q)
        else:
            path = "legacy"
            trigger_term_set.update(t for t in r.include_any if t in q)
        trigger_paths_list.append((r.intent, path))
    trigger_terms = sorted(trigger_term_set)

    # Near-miss routes: broad terms matched but context gate failed.
    # Most useful signal for tuning - tells you a route "almost" fired.
    near_misses: list[tuple[str, tuple[str, ...]]] = []
    for route in routes:
        if route.intent in set(r.intent for r in matched):
            continue
        if route.exclude_any and any(t in q for t in route.exclude_any):
            continue
        if route.include_any_broad and any(t in q for t in route.include_any_broad):
            if not any(t in q for t in route.require_context_any):
                broad_matched = tuple(t for t in route.include_any_broad if t in q)
                near_misses.append((route.intent, broad_matched))

    # Dominance audit fields
    dominant_route = ""
    dominance_reason = ""
    ignored: list[tuple[str, str]] = []
    if matched and dominant is not None:
        dominant_route = dominant.intent
        if allow_candidates:
            parts_d = ["has leg_allow_list"]
            if dominant.priority > 0:
                parts_d.append(f"priority {dominant.priority}")
            dominance_reason = ", ".join(parts_d)
            for r in matched:
                if r is dominant:
                    continue
                why = (
                    f"lower priority ({r.priority} < {dominant.priority}); "
                    "allow-list unioned, forced sections merged"
                    if r.leg_allow_list
                    else "no allow-list; forced sections merged"
                )
                ignored.append((r.intent, why))
        else:
            dominance_reason = (
                f"highest priority ({dominant.priority}); "
                "no matched routes define leg_allow_list"
            )
            ignored = [
                (r.intent, "lower priority; forced sections still merged")
                for r in matched if r is not dominant
            ]

    return RouteDecision(
        triggered=bool(matched),
        matched_intents=tuple(r.intent for r in matched),
        trigger_terms=tuple(trigger_terms),
        trigger_paths=tuple(trigger_paths_list),
        forced_sections=tuple(forced),
        leg_allow_list=leg_allow_list,
        boosted_act_ids=frozenset(boosted),
        leg_synthetic_queries=tuple(leg_synths),
        case_synthetic_queries=tuple(case_synths),
        dominant_route=dominant_route,
        dominance_reason=dominance_reason,
        ignored_routes=tuple(ignored),
        near_miss_routes=tuple(near_misses),
    )


def allow_section(
    case_id: str,
    combined_query: str,
    low_priority_sections: dict[str, tuple[str, ...]],
) -> bool:
    """Return False to suppress sections that are almost-never relevant for this query."""
    rule = low_priority_sections.get(case_id)
    if not rule:
        return True
    return any(term in combined_query for term in rule)
