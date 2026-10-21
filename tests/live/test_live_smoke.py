"""Manual live smoke against the real Zoho API: read-only GETs only, skipped unless ZOHO_AXI_LIVE=1.

    ZOHO_AXI_LIVE=1 venv/bin/python -m pytest tests/live -q

Needs real credentials (config file or ZOHO_AXI_* environment variables). Never part of the default
suite or CI; see tests/README.md. Prints nothing sensitive: assertions are on counts and keys only.
"""
import pytest

from zoho_axi.core.config import load_settings
from zoho_axi.core.gateway import ZohoGateway

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def gateway(settings):
    return ZohoGateway(settings)


def test_portals_are_listed(gateway):
    portals = gateway.get("portals")
    assert len(portals) >= 1
    assert all(p.get("id") for p in portals)


def test_the_default_project_lists_a_page_of_tasks(settings, gateway):
    if not (settings.portal_id and settings.project_id):
        pytest.skip("portal_id/project_id not configured")
    tasks = gateway.get("tasks", portal=settings.portal_id, project=settings.project_id, params={"per_page": "5"})
    assert len(tasks) <= 5
    assert gateway.request_count >= 1
