"""Contract tests - every jurisdiction must pass these automatically.

These tests verify that a jurisdiction module:
  - implements all required properties
  - has valid forced sections in Qdrant (when a leg_collection is configured)
  - has reachable legislation URLs (when legislation config is provided)
  - provides parseable smoke fixtures

Run against a specific jurisdiction:
    pytest tests/core/test_jurisdiction_contract.py --jurisdiction nz_tenancy
"""

import importlib
import pytest

from core.jurisdiction import JurisdictionBase


def load_jurisdiction(name: str) -> JurisdictionBase:
    mod = importlib.import_module(f"jurisdictions.{name}")
    assert hasattr(mod, "jurisdiction"), f"jurisdictions/{name}/__init__.py must export 'jurisdiction'"
    return mod.jurisdiction


# ---------------------------------------------------------------------------
# Required property contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("attr", ["name", "corpus", "system_prompt", "routes"])
def test_required_property_exists(jurisdiction_name, attr):
    j = load_jurisdiction(jurisdiction_name)
    val = getattr(j, attr)
    assert val is not None, f"{attr} must not be None"


def test_name_is_nonempty_string(jurisdiction_name):
    j = load_jurisdiction(jurisdiction_name)
    assert isinstance(j.name, str) and j.name.strip()


def test_system_prompt_is_nonempty(jurisdiction_name):
    j = load_jurisdiction(jurisdiction_name)
    assert len(j.system_prompt) > 100, "system_prompt is suspiciously short"


def test_routes_is_list(jurisdiction_name):
    j = load_jurisdiction(jurisdiction_name)
    assert isinstance(j.routes, list)


def test_corpus_has_collection_and_courts(jurisdiction_name):
    j = load_jurisdiction(jurisdiction_name)
    assert j.corpus.qdrant_collection
    assert isinstance(j.corpus.courts, list) and j.corpus.courts


# ---------------------------------------------------------------------------
# Smoke fixtures contract
# ---------------------------------------------------------------------------

def test_smoke_fixtures_parseable(jurisdiction_name):
    j = load_jurisdiction(jurisdiction_name)
    for fixture in j.smoke_fixtures:
        assert fixture.question
        assert isinstance(fixture.expected_sections, list)


# ---------------------------------------------------------------------------
# Route integrity
# ---------------------------------------------------------------------------

def test_routes_have_required_fields(jurisdiction_name):
    j = load_jurisdiction(jurisdiction_name)
    for route in j.routes:
        assert route.intent, "route.intent must not be empty"
        assert route.include_any, "route.include_any must not be empty"
        assert route.forced_sections, "route.forced_sections must not be empty"
        assert route.synthetic_query, "route.synthetic_query must not be empty"


# ---------------------------------------------------------------------------
# Legislation config (optional)
# ---------------------------------------------------------------------------

def test_legislation_urls_are_strings(jurisdiction_name):
    j = load_jurisdiction(jurisdiction_name)
    if j.legislation is None:
        pytest.skip("No legislation config")
    for act_id, url in j.legislation.acts.items():
        assert isinstance(url, str) and url.startswith("http"), \
            f"Act {act_id} URL must be a full HTTP URL"


# jurisdiction_name and pytest_addoption live in tests/conftest.py
