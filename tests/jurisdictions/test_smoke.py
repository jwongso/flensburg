"""Tier 1 smoke tests - retrieval only, no LLM.

Calls /retrieve for each SmokeFixture in jurisdiction.smoke_fixtures and
asserts that expected legislation sections appear in the result.

Run:
    pytest tests/jurisdictions/test_smoke.py --jurisdiction nz_tenancy -m retrieval -v
    pytest tests/jurisdictions/test_smoke.py --jurisdiction nz_employment -m retrieval -v
"""

import pytest

from tests.conftest import skip_no_qdrant


def _leg_ids(response_json: dict) -> set[str]:
    return {s["case_id"] for s in response_json.get("legislation", [])}


def _source_ids(response_json: dict) -> set[str]:
    return {s["case_id"] for s in response_json.get("sources", [])}


def _retrieve(client, question: str) -> dict:
    r = client.post("/retrieve", json={"question": question})
    assert r.status_code == 200, f"/retrieve failed: {r.status_code} {r.text[:300]}"
    return r.json()


# ---------------------------------------------------------------------------
# Tier 1: legislation anchor retrieval
# ---------------------------------------------------------------------------

@skip_no_qdrant
@pytest.mark.retrieval
class TestSmokeFixtures:
    """Run every SmokeFixture defined in the jurisdiction."""

    def test_fixtures_exist(self, jurisdiction):
        assert jurisdiction.smoke_fixtures, \
            f"{jurisdiction.name} has no smoke_fixtures - add at least one SmokeFixture"

    @pytest.mark.parametrize("fixture_index", range(25))
    def test_smoke_fixture(self, app_client, jurisdiction, fixture_index):
        fixtures = jurisdiction.smoke_fixtures
        if fixture_index >= len(fixtures):
            pytest.skip(f"No fixture at index {fixture_index}")

        fixture = fixtures[fixture_index]
        desc = fixture.description or fixture.question[:60]

        if not jurisdiction.corpus.leg_collection:
            pytest.skip(f"{jurisdiction.name} has no leg_collection - skipping legislation tests")

        result = _retrieve(app_client, fixture.question)
        ids = _leg_ids(result)

        missing = [s for s in fixture.expected_sections if s not in ids]
        assert not missing, (
            f"[{desc}] Expected sections missing from leg_sources.\n"
            f"  Missing: {missing}\n"
            f"  Got: {sorted(ids)}\n"
            f"  Question: {fixture.question}"
        )

        present_forbidden = [s for s in fixture.forbidden_sections if s in ids]
        assert not present_forbidden, (
            f"[{desc}] Forbidden sections appeared in leg_sources.\n"
            f"  Forbidden present: {present_forbidden}\n"
            f"  Got: {sorted(ids)}\n"
            f"  Question: {fixture.question}"
        )

        if fixture.min_sources > 0:
            n = len(_source_ids(result))
            assert n >= fixture.min_sources, (
                f"[{desc}] Expected at least {fixture.min_sources} case sources, got {n}.\n"
                f"  Question: {fixture.question}"
            )

        if fixture.expected_guidance_sources:
            guidance = result.get("guidance")
            assert guidance is not None, (
                f"[{desc}] Expected guidance injection but /retrieve returned no 'guidance' field.\n"
                f"  Expected one of: {fixture.expected_guidance_sources}\n"
                f"  Question: {fixture.question}"
            )
            got_source = guidance.get("source")
            assert got_source in fixture.expected_guidance_sources, (
                f"[{desc}] Guidance source mismatch.\n"
                f"  Expected one of: {fixture.expected_guidance_sources}\n"
                f"  Got: {got_source!r} (reason={guidance.get('reason')})\n"
                f"  Question: {fixture.question}"
            )


# ---------------------------------------------------------------------------
# Tier 1: refine-retrieve integration
# ---------------------------------------------------------------------------

@skip_no_qdrant
@pytest.mark.retrieval
class TestRefineRetrieve:
    """Verify _refine_retrieve does not corrupt normal results and fires on poor queries."""

    def test_high_confidence_query_unaffected(self, app_client, jurisdiction):
        """A clearly scoped query should still return sources after refine path was added."""
        if not jurisdiction.smoke_fixtures:
            pytest.skip("No smoke fixtures")
        q = jurisdiction.smoke_fixtures[0].question
        result = _retrieve(app_client, q)
        assert len(result.get("sources", [])) >= 2, (
            "High-confidence query returned fewer than 2 sources - "
            "refine may have corrupted the result set"
        )

    def test_vague_query_returns_something(self, app_client, jurisdiction):
        """A vague query should still return at least one source after refine fallback."""
        if not jurisdiction.smoke_fixtures:
            pytest.skip("No smoke fixtures")
        result = _retrieve(app_client, "my landlord won't help me")
        sources = result.get("sources", [])
        assert len(sources) >= 1, (
            "Vague query returned no sources even after refine fallback. "
            "Check _refine_retrieve thresholds."
        )

    def test_sources_have_valid_scores_after_refine(self, app_client, jurisdiction):
        """Sources returned after a potential refine pass must still have required fields."""
        if not jurisdiction.smoke_fixtures:
            pytest.skip("No smoke fixtures")
        result = _retrieve(app_client, "my landlord won't help me")
        for src in result.get("sources", []):
            assert "case_id" in src
            assert "url" in src


# ---------------------------------------------------------------------------
# Tier 1: case retrieval sanity
# ---------------------------------------------------------------------------

@skip_no_qdrant
@pytest.mark.retrieval
class TestCaseRetrieval:
    """Basic checks that the case corpus is searchable."""

    def test_retrieval_returns_sources(self, app_client, jurisdiction):
        if not jurisdiction.smoke_fixtures:
            pytest.skip("No smoke fixtures to derive a test question from")
        q = jurisdiction.smoke_fixtures[0].question
        result = _retrieve(app_client, q)
        assert result.get("sources"), \
            f"No sources returned for question: {q!r}"

    def test_retrieval_sources_have_required_fields(self, app_client, jurisdiction):
        if not jurisdiction.smoke_fixtures:
            pytest.skip("No smoke fixtures")
        q = jurisdiction.smoke_fixtures[0].question
        result = _retrieve(app_client, q)
        for src in result.get("sources", []):
            assert "case_id" in src, f"Source missing case_id: {src}"
            assert "url" in src, f"Source missing url: {src}"

    def test_retrieval_courts_match_corpus(self, app_client, jurisdiction):
        """All returned sources should come from the jurisdiction's declared courts."""
        if not jurisdiction.smoke_fixtures:
            pytest.skip("No smoke fixtures")
        if not jurisdiction.corpus.courts:
            pytest.skip("No court filter declared")
        q = jurisdiction.smoke_fixtures[0].question
        result = _retrieve(app_client, q)
        allowed = set(jurisdiction.corpus.courts)
        for src in result.get("sources", []):
            cid = src.get("case_id", "")
            court = cid.split("/")[0] if "/" in cid else ""
            assert not court or court in allowed or court == "NZLEG", \
                f"Source from unexpected court: {cid}"
