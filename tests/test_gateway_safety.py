"""Read-only guard, download allowlist/cap, and the token-refresh path, all through the gateway seams
(S1 get, S3 download) with the replay transport at S2."""
import pytest
from support.factories import API, TOKEN_URL, Clock, make_settings
from support.replay import ReplayTransport

from zoho_axi.core.auth import ZohoAuth
from zoho_axi.core.errors import NotFoundError, RateLimitedError, ReadOnlyError, UpstreamError
from zoho_axi.core.gateway import ZohoGateway
from zoho_axi.core.transport import ReadOnlyTransport

FILE = "https://projects.zoho.com/files/shot.png"


class CountingToken:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return "tok-secret"


def build(tmp_path, replay, **kwargs):
    token = kwargs.pop("token", None) or CountingToken()
    clock = Clock()
    gateway = ZohoGateway(
        make_settings(tmp_path, **kwargs.pop("env", {})), transport=replay, token_provider=token,
        clock=clock, sleep=lambda s: None, **kwargs,
    )
    return gateway, token


# --- read-only guard ---

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "Delete"])
def test_non_get_requests_are_refused_before_any_io(method):
    replay = ReplayTransport().add(method, f"{API}/portals", json={})
    guarded = ReadOnlyTransport(replay, allow_post_urls=(TOKEN_URL,))
    with pytest.raises(ReadOnlyError):
        guarded.send(method, f"{API}/portals", {}, None, 5)
    assert replay.calls == []


def test_a_post_is_refused_even_to_a_zoho_host_unless_it_is_the_token_endpoint(tmp_path):
    replay = ReplayTransport()
    gateway, token = build(tmp_path, replay)
    for url in (f"{API}/portals", "https://accounts.zoho.com/oauth/v2/other", "https://evil.example/oauth/v2/token"):
        with pytest.raises(ReadOnlyError):
            gateway.token_post(url, {"grant_type": "refresh_token"}, 5)
    assert replay.calls == []


def test_get_and_download_only_ever_issue_gets(tmp_path):
    replay = ReplayTransport().get(f"{API}/portals", json={}).get(FILE, body=b"x")
    gateway, _ = build(tmp_path, replay)
    gateway.get("portals")
    gateway.download(FILE, max_bytes=10)
    assert {c.method for c in replay.calls} == {"GET"}


# --- downloads: allowlist, token handling, cap ---

def test_download_returns_bytes_and_content_type_and_sends_the_token(tmp_path):
    replay = ReplayTransport().get(FILE, body=b"\x89PNGdata", content_type="image/png")
    gateway, _ = build(tmp_path, replay)
    assert gateway.download(FILE, max_bytes=1024) == (b"\x89PNGdata", "image/png")
    assert replay.calls[0].headers == {"Authorization": "Zoho-oauthtoken tok-secret"}
    assert replay.calls[0].max_bytes == 1024


@pytest.mark.parametrize("url", [
    "https://evil.example/shot.png",
    "https://projects.zoho.com.evil.example/shot.png",
    "https://evilzoho.com/shot.png",
    "https://projects.zoho.eu/shot.png",  # a different data center than the configured one
    "https://evil.example/?next=https://projects.zoho.com/x",
    "https://projects.zoho.com@evil.example/shot.png",
    "http://projects.zoho.com/shot.png",  # plain http would expose the token
    "ftp://projects.zoho.com/shot.png",
    "projects.zoho.com/shot.png",
    "/relative/path.png",
    "",
])
def test_the_token_is_never_sent_to_a_non_zoho_host(tmp_path, url):
    replay = ReplayTransport()
    gateway, token = build(tmp_path, replay)
    with pytest.raises(UpstreamError) as exc:
        gateway.download(url, max_bytes=1024)
    assert exc.value.code == "download.host_refused"
    assert replay.calls == []  # nothing went out ...
    assert token.calls == 0  # ... and the token was never even read
    assert "tok-secret" not in exc.value.message + (exc.value.hint or "")


@pytest.mark.parametrize("url", [
    "https://projects.zoho.com/a.png",
    "https://zoho.com/a.png",
    "https://workdrive.zohopublic.com/a.png",
    "https://zohopublic.com/a.png",
    "https://PROJECTS.ZOHO.COM/a.png",
])
def test_zoho_hosts_of_the_configured_data_center_are_allowed(tmp_path, url):
    replay = ReplayTransport().get(url, body=b"ok")
    gateway, _ = build(tmp_path, replay)
    assert gateway.download(url, max_bytes=10)[0] == b"ok"


def test_the_allowlist_follows_the_configured_data_center(tmp_path):
    url = "https://projects.zoho.eu/a.png"
    replay = ReplayTransport().get(url, body=b"eu")
    gateway, _ = build(tmp_path, replay, env={"ZOHO_AXI_DC": "eu"})
    assert gateway.download(url, max_bytes=10)[0] == b"eu"


def test_a_body_over_the_cap_is_an_upstream_error_not_returned(tmp_path):
    replay = ReplayTransport().get(FILE, body=b"x" * 11)
    gateway, _ = build(tmp_path, replay)
    with pytest.raises(UpstreamError) as exc:
        gateway.download(FILE, max_bytes=10)
    assert exc.value.code == "download.too_large"
    assert "10" in exc.value.message


def test_an_adapter_that_ignores_the_cap_is_still_caught_by_the_gateway(tmp_path):
    class Careless:
        def send(self, *args, **kwargs):
            from zoho_axi.core.transport import TransportResponse

            return TransportResponse(200, b"x" * 50, "image/png")

    gateway = ZohoGateway(make_settings(tmp_path), transport=Careless(), token_provider=lambda: "t")
    with pytest.raises(UpstreamError):
        gateway.download(FILE, max_bytes=10)


def test_the_cap_defaults_to_the_configured_attachment_limit(tmp_path):
    replay = ReplayTransport().get(FILE, body=b"ok")
    gateway, _ = build(tmp_path, replay, env={"ZOHO_AXI_MAX_ATTACHMENT_BYTES": "1"})
    with pytest.raises(UpstreamError):
        gateway.download(FILE)


def test_download_errors_are_classified_like_api_errors(tmp_path):
    replay = ReplayTransport().get(FILE, status=404)
    gateway, _ = build(tmp_path, replay)
    with pytest.raises(NotFoundError):
        gateway.download(FILE, max_bytes=10)
    replay = ReplayTransport().get(FILE, status=500)
    gateway, _ = build(tmp_path, replay)
    with pytest.raises(UpstreamError):
        gateway.download(FILE, max_bytes=10)


def test_downloads_obey_429_retry(tmp_path):
    replay = ReplayTransport().get(FILE, status=429).get(FILE, body=b"ok", content_type="image/png")
    gateway, _ = build(tmp_path, replay)
    assert gateway.download(FILE, max_bytes=10) == (b"ok", "image/png")
    assert len(replay.calls) == 2


def test_downloads_share_the_request_budget_with_api_calls(tmp_path):
    replay = ReplayTransport().get(f"{API}/portals", json={}, repeat=True).get(FILE, body=b"x", repeat=True)
    gateway, _ = build(tmp_path, replay, rate_limit=3)
    gateway.get("portals")
    gateway.download(FILE, max_bytes=10)
    gateway.get("portals")
    with pytest.raises(RateLimitedError):
        gateway.download(FILE, max_bytes=10)
    assert gateway.request_count == 3


# --- token refresh goes through the same gateway ---

def test_without_an_injected_token_the_gateway_refreshes_through_its_own_transport(tmp_path):
    replay = ReplayTransport()
    replay.add("POST", TOKEN_URL, json={"access_token": "tok-fresh", "expires_in": 3600})
    replay.get(f"{API}/portals", json={"portals": [{"id": 1}]})
    gateway = ZohoGateway(make_settings(tmp_path), transport=replay, clock=Clock(), sleep=lambda s: None)

    assert gateway.get("portals") == [{"id": "1"}]

    refresh, api = replay.calls
    assert refresh.method == "POST" and refresh.url == TOKEN_URL
    assert refresh.data["grant_type"] == "refresh_token"
    assert refresh.headers == {}  # the token request carries no Authorization header
    assert refresh.params is None  # secrets travel in the body, never the URL
    assert api.headers == {"Authorization": "Zoho-oauthtoken tok-fresh"}
    assert gateway.request_count == 2


def test_a_429_on_the_token_endpoint_is_a_rate_limit_error_not_an_auth_failure(tmp_path):
    replay = ReplayTransport().add("POST", TOKEN_URL, status=429, repeat=True)
    gateway = ZohoGateway(make_settings(tmp_path), transport=replay, clock=Clock(), sleep=lambda s: None)
    with pytest.raises(RateLimitedError) as exc:
        gateway.get("portals")
    assert exc.value.exit_code == 6
    assert len(replay.calls) == 3  # retried, and nothing was sent to the API


def test_zohos_too_many_requests_body_on_the_token_endpoint_is_a_rate_limit_error(tmp_path):
    body = {"error": "Access Denied", "error_description": "You have made too many requests continuously."}
    replay = ReplayTransport().add("POST", TOKEN_URL, status=400, json=body)
    gateway = ZohoGateway(make_settings(tmp_path), transport=replay, clock=Clock(), sleep=lambda s: None)
    with pytest.raises(RateLimitedError) as exc:
        gateway.get("portals")
    assert exc.value.code == "rate_limited.token"
    assert exc.value.exit_code == 6


def test_the_token_refresh_counts_against_the_budget(tmp_path):
    replay = ReplayTransport()
    replay.add("POST", TOKEN_URL, json={"access_token": "t", "expires_in": 3600})
    replay.get(f"{API}/portals", json={}, repeat=True)
    gateway = ZohoGateway(make_settings(tmp_path), transport=replay, rate_limit=2, clock=Clock(), sleep=lambda s: None)
    gateway.get("portals")  # refresh + get = 2
    with pytest.raises(RateLimitedError):
        gateway.get("portals")


def test_zohoauth_with_an_injected_gateway_post_classifies_throttling(tmp_path):
    replay = ReplayTransport().add("POST", TOKEN_URL, status=429, repeat=True)
    settings = make_settings(tmp_path)
    gateway = ZohoGateway(settings, transport=replay, clock=Clock(), sleep=lambda s: None)
    with pytest.raises(RateLimitedError):
        ZohoAuth(settings, post=gateway.token_post).get_access_token()


def test_no_token_or_credential_reaches_logs_or_stdout(tmp_path, caplog, capsys):
    import logging

    caplog.set_level(logging.DEBUG)
    replay = ReplayTransport()
    replay.add("POST", TOKEN_URL, json={"access_token": "tok-never-log", "expires_in": 3600})
    replay.get(f"{API}/portals", status=429).get(f"{API}/portals", json={})
    replay.get(FILE, body=b"ok")
    gateway = ZohoGateway(make_settings(tmp_path), transport=replay, clock=Clock(), sleep=lambda s: None)
    gateway.get("portals")
    gateway.download(FILE, max_bytes=10)

    out = capsys.readouterr()
    for secret in ("tok-never-log", "super-secret-value", "refresh-token-value"):
        assert secret not in caplog.text + out.out + out.err
    assert out.out == ""  # stdout is result-only; the gateway never prints


def test_a_non_json_token_reply_becomes_an_error_body_for_auth_to_report(tmp_path):
    replay = ReplayTransport().add("POST", TOKEN_URL, status=502, body=b"<html>bad gateway</html>")
    gateway = ZohoGateway(make_settings(tmp_path), transport=replay, clock=Clock(), sleep=lambda s: None)
    assert gateway.token_post(TOKEN_URL, {"grant_type": "refresh_token"}, 5) == {
        "error": "non-JSON response (HTTP 502)"
    }
