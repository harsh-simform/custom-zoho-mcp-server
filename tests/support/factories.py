"""Small builders shared by gateway/auth tests."""
from __future__ import annotations

from zoho_projects_axi.core.config import load_settings
from zoho_projects_axi.core.gateway import ZohoGateway

API = "https://projectsapi.zoho.com/api/v3"
LEGACY = "https://projectsapi.zoho.com/restapi"
TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"


def make_settings(tmp_path, **env_extra):
    env = {
        "HOME": str(tmp_path),
        "ZOHO_PROJECTS_AXI_CLIENT_ID": "cid-123",
        "ZOHO_PROJECTS_AXI_CLIENT_SECRET": "super-secret-value",
        "ZOHO_PROJECTS_AXI_REFRESH_TOKEN": "refresh-token-value",
    }
    env.update(env_extra)
    return load_settings(env=env, legacy_env_file=tmp_path / "none.env")


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_gateway(tmp_path, transport, **kwargs) -> ZohoGateway:
    """A gateway over `transport` (usually a ReplayTransport) with a fixed token, no real sleeping and a
    frozen clock, so retries and the limiter are instant and deterministic."""
    kwargs.setdefault("token_provider", lambda: "tok-abc")
    kwargs.setdefault("sleep", lambda seconds: None)
    kwargs.setdefault("clock", Clock())
    return ZohoGateway(make_settings(tmp_path), transport=transport, **kwargs)
