"""TLS behind a re-signing proxy (Zscaler and the like): the OS trust store is used, and a failed request says why."""
import sys
import types

import pytest

from zoho_projects_axi.core import http_transport
from zoho_projects_axi.core.auth import ZohoAuth
from zoho_projects_axi.core.config import load_settings
from zoho_projects_axi.core.errors import UpstreamError
from zoho_projects_axi.core.transport import TransportError, describe_transport_error, unreachable_hint


def test_the_os_trust_store_is_injected_once_before_the_first_request(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "truststore", types.SimpleNamespace(inject_into_ssl=lambda: calls.append(1)))
    monkeypatch.setattr(http_transport, "_trust_store_ready", False)
    http_transport._use_system_trust_store()
    http_transport._use_system_trust_store()
    assert calls == [1]


def test_a_missing_or_failing_truststore_falls_back_to_the_bundled_certificates(monkeypatch):
    def boom():
        raise RuntimeError("unsupported platform")

    monkeypatch.setitem(sys.modules, "truststore", types.SimpleNamespace(inject_into_ssl=boom))
    monkeypatch.setattr(http_transport, "_trust_store_ready", False)
    http_transport._use_system_trust_store()  # must not raise
    monkeypatch.setitem(sys.modules, "truststore", None)  # import fails
    monkeypatch.setattr(http_transport, "_trust_store_ready", False)
    http_transport._use_system_trust_store()


def test_the_cause_of_a_transport_failure_is_named_without_the_url():
    assert describe_transport_error(TransportError("SSLError")) == "SSLError"
    assert describe_transport_error(ConnectionResetError("secret detail")) == "ConnectionResetError"


def test_a_tls_failure_gets_a_proxy_hint_and_other_failures_keep_the_network_hint():
    assert "SSL_CERT_FILE" in unreachable_hint(TransportError("SSLError"))
    assert "network connection" in unreachable_hint(TransportError("ConnectTimeout"))


def test_the_token_refresh_error_names_the_tls_cause(tmp_path):
    settings = load_settings(env={"HOME": str(tmp_path), "ZOHO_PROJECTS_AXI_CLIENT_ID": "i",
                                  "ZOHO_PROJECTS_AXI_CLIENT_SECRET": "s", "ZOHO_PROJECTS_AXI_REFRESH_TOKEN": "r"},
                             legacy_env_file=tmp_path / "none.env")

    def post(url, data, timeout):
        raise TransportError("SSLError")

    with pytest.raises(UpstreamError) as err:
        ZohoAuth(settings, post=post).get_access_token()
    assert err.value.message == "Could not reach https://accounts.zoho.com: SSLError"
    assert "SSL_CERT_FILE" in err.value.hint
