"""Regression tests for allow-list union behavior in build_route_decision().

When multiple routes with leg_allow_list fire on the same query, the resulting
leg_allow_list must be the union of all matched allow-lists - not just the
highest-priority route's list. A multi-issue query needs all relevant statute
sections to remain accessible.
"""

import pytest

from core.routing import StatuteRoute, build_route_decision


_PROPERTY_CHANGE = StatuteRoute(
    intent="property_change",
    include_any=("carport",),
    forced_sections=("NZLEG/RTA/s40", "NZLEG/RTA/s42A", "NZLEG/RTA/s42B"),
    synthetic_query="property alteration consent fixture",
    leg_allow_list=("NZLEG/RTA/s40", "NZLEG/RTA/s42A", "NZLEG/RTA/s42B"),
    priority=10,
)

_CARPARK_DISPUTE = StatuteRoute(
    intent="carpark_dispute",
    include_any=("parking space",),
    forced_sections=("NZLEG/RTA/s45", "NZLEG/RTA/s13A"),
    synthetic_query="carpark landlord obligation agreed facility",
    leg_allow_list=("NZLEG/RTA/s45", "NZLEG/RTA/s13A"),
    priority=8,
)

_ROUTES = [_PROPERTY_CHANGE, _CARPARK_DISPUTE]


class TestAllowListUnion:
    def test_single_route_allow_list_unchanged(self):
        decision = build_route_decision(
            "I want to install a carport without consent",
            "",
            _ROUTES,
        )
        assert set(decision.matched_intents) == {"property_change"}
        assert set(decision.leg_allow_list) == {"NZLEG/RTA/s40", "NZLEG/RTA/s42A", "NZLEG/RTA/s42B"}

    def test_single_carpark_allow_list_unchanged(self):
        decision = build_route_decision(
            "My landlord removed our parking space",
            "",
            _ROUTES,
        )
        assert set(decision.matched_intents) == {"carpark_dispute"}
        assert set(decision.leg_allow_list) == {"NZLEG/RTA/s45", "NZLEG/RTA/s13A"}

    def test_multi_route_allow_list_is_union(self):
        decision = build_route_decision(
            "My landlord removed our parking space and I want to install a carport without consent",
            "",
            _ROUTES,
        )
        assert "property_change" in decision.matched_intents
        assert "carpark_dispute" in decision.matched_intents
        assert set(decision.leg_allow_list) == {
            "NZLEG/RTA/s40",
            "NZLEG/RTA/s42A",
            "NZLEG/RTA/s42B",
            "NZLEG/RTA/s45",
            "NZLEG/RTA/s13A",
        }

    def test_dominant_route_is_still_highest_priority(self):
        decision = build_route_decision(
            "My landlord removed our parking space and I want to install a carport without consent",
            "",
            _ROUTES,
        )
        assert decision.dominant_route == "property_change"

    def test_no_allow_list_routes_gives_empty(self):
        route_no_list = StatuteRoute(
            intent="repairs_maintenance",
            include_any=("broken",),
            forced_sections=("NZLEG/RTA/s45",),
            synthetic_query="repair maintenance landlord obligation",
        )
        decision = build_route_decision("something is broken", "", [route_no_list])
        assert decision.leg_allow_list == ()
