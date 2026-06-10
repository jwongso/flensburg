"""Root conftest - shared fixtures and CLI options for all Astraea tests."""

import importlib
from pathlib import Path

import httpx
import pytest


def pytest_configure(config):
    """Inject .training/test_local_fixtures.py into the collection path if present.

    The file is gitignored (local-only training pipeline tests derived from real
    community questions). Adding it here means a plain 'pytest tests/' run picks
    it up automatically on machines that have the training data pipeline set up,
    without any changes needed on fresh clones or CI.
    """
    local = Path(".training/test_local_fixtures.py")
    if local.exists():
        config.args = list(config.args) + [str(local)]


def pytest_addoption(parser):
    parser.addoption(
        "--jurisdiction",
        action="store",
        default=None,
        help="Jurisdiction module name to test, e.g. nz_tenancy",
    )


def _qdrant_available() -> bool:
    import os
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    try:
        return httpx.get(f"{url}/collections", timeout=3).status_code == 200
    except Exception:
        return False


def _llm_available() -> bool:
    import os
    url = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
    try:
        return httpx.get(f"{url}/models", timeout=3).status_code == 200
    except Exception:
        return False


qdrant_available = _qdrant_available()
llm_available = _llm_available()

skip_no_qdrant = pytest.mark.skipif(not qdrant_available, reason="Qdrant not available")
skip_no_llm = pytest.mark.skipif(not llm_available, reason="LLM server not running")


@pytest.fixture(scope="session")
def jurisdiction_name(request) -> str:
    name = request.config.getoption("--jurisdiction")
    if not name:
        pytest.skip("Pass --jurisdiction <name> to run jurisdiction tests")
    return name


@pytest.fixture(scope="session")
def jurisdiction(jurisdiction_name):
    mod = importlib.import_module(f"jurisdictions.{jurisdiction_name}")
    assert hasattr(mod, "jurisdiction"), \
        f"jurisdictions/{jurisdiction_name}/__init__.py must export 'jurisdiction'"
    return mod.jurisdiction


@pytest.fixture(scope="session")
def app_client(jurisdiction):
    from fastapi.testclient import TestClient
    from core.api import create_app
    import os
    with TestClient(create_app(jurisdiction)) as client:
        token = os.getenv("PUBLIC_TOKEN", "")
        headers = {"X-No-Log": "1"}
        if token:
            headers["X-API-Key"] = token
        client.headers.update(headers)
        yield client
