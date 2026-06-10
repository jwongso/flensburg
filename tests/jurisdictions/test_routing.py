"""Pure routing unit tests - no Qdrant, no HTTP, no LLM.

Exercises build_route_decision() against every RouteFixture defined in the
jurisdiction. Tests run in milliseconds and catch keyword collision regressions
before they reach users.

Run:
    pytest tests/jurisdictions/test_routing.py --jurisdiction nz_tenancy -v
"""

import pytest

from core.routing import build_route_decision


@pytest.mark.routing
class TestRouteFixtures:
    """One positive + one negative per route, parameterized over all fixtures."""

    def test_route_fixtures_exist(self, jurisdiction):
        assert jurisdiction.route_fixtures, (
            f"{jurisdiction.name} has no route_fixtures - add at least one RouteFixture "
            f"per route (positive + negative)"
        )

    @pytest.mark.parametrize("fixture_index", range(40))
    def test_route_fixture(self, jurisdiction, fixture_index):
        fixtures = jurisdiction.route_fixtures
        if fixture_index >= len(fixtures):
            pytest.skip(f"No fixture at index {fixture_index}")

        fx = fixtures[fixture_index]
        desc = fx.description or fx.question[:70]
        rewritten = fx.rewritten or fx.question

        decision = build_route_decision(fx.question, rewritten, jurisdiction.routes)
        matched = set(decision.matched_intents)

        missing = [r for r in fx.expected_routes if r not in matched]
        assert not missing, (
            f"[{desc}]\n"
            f"  Expected routes did not fire: {missing}\n"
            f"  Fired: {sorted(matched)}\n"
            f"  Trigger terms: {list(decision.trigger_terms)}\n"
            f"  Question: {fx.question}"
        )

        present_forbidden = [r for r in fx.forbidden_routes if r in matched]
        assert not present_forbidden, (
            f"[{desc}]\n"
            f"  Forbidden routes fired: {present_forbidden}\n"
            f"  Fired: {sorted(matched)}\n"
            f"  Trigger terms: {list(decision.trigger_terms)}\n"
            f"  Question: {fx.question}"
        )
