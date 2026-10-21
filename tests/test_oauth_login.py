"""Browser sign-in (`auth login`): PKCE, the loopback callback, state checking and writing the token.
The "browser" is a thread that requests the callback URL; Zoho's token endpoint is a fake `post`."""
import base64
import hashlib
import socket
import threading
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from zoho_axi.core.config import load_settings
from zoho_axi.core.config_edit import set_refresh_token
from zoho_axi.core.errors import AuthError, ConfigError, UpstreamError, UsageError
from zoho_axi.core import oauth_login
from zoho_axi.core.oauth_login import authorize, pkce_pair

CLIENT = {"ZOHO_AXI_CLIENT_ID": "cid-123", "ZOHO_AXI_CLIENT_SECRET": "csecret-456"}


def make_settings(tmp_path, **env):
    return load_settings(env={"HOME": str(tmp_path), **env}, legacy_env_file=tmp_path / "none.env")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakePost:
    def __init__(self, body):
        self.body, self.calls = body, []

    def __call__(self, url, data, timeout):
        self.calls.append((url, dict(data)))
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def _visit(url):
    try:
        urllib.request.urlopen(url, timeout=5).read()
    except OSError:  # the listener answers 400 to a denied or forged callback
        pass


def browser(callback_query, seen):
    """An `open_browser` that records the consent URL and plays the redirect back to the listener."""
    def open_url(url):
        query = parse_qs(urlsplit(url).query)
        seen.update({k: v[0] for k, v in query.items()})
        params = callback_query(seen)
        target = f"{seen['redirect_uri']}?" + "&".join(f"{k}={v}" for k, v in params.items())
        threading.Thread(target=_visit, args=(target,), daemon=True).start()
        return True
    return open_url


def run(tmp_path, post, callback_query, **env):
    seen: dict = {}
    port = free_port()
    token = authorize(make_settings(tmp_path, **CLIENT, **env), post, port=port, announce=lambda _l: None,
                      open_browser=browser(callback_query, seen), timeout=10)
    return token, seen


def good(seen):
    return {"code": "auth-code-1", "state": seen["state"]}


def test_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_login_requests_offline_read_only_access_and_exchanges_the_code(tmp_path):
    post = FakePost({"access_token": "a", "refresh_token": "1000.refresh-xyz"})
    token, seen = run(tmp_path, post, good)

    assert token == "1000.refresh-xyz"
    assert seen["access_type"] == "offline" and seen["prompt"] == "consent"
    assert seen["code_challenge_method"] == "S256"
    assert seen["client_id"] == "cid-123"
    assert "client_secret" not in seen
    assert all(s.endswith(".READ") for s in seen["scope"].split(","))
    url, data = post.calls[0]
    assert url == "https://accounts.zoho.com/oauth/v2/token"
    assert data["grant_type"] == "authorization_code" and data["code"] == "auth-code-1"
    assert data["redirect_uri"] == seen["redirect_uri"]
    expected = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert seen["code_challenge"] == expected


def test_state_mismatch_saves_nothing_and_skips_the_exchange(tmp_path):
    post = FakePost({"refresh_token": "never"})
    with pytest.raises(AuthError) as err:
        run(tmp_path, post, lambda seen: {"code": "c", "state": "forged"})
    assert err.value.code == "auth.state_mismatch"
    assert post.calls == []


def test_denied_consent_is_an_auth_error(tmp_path):
    with pytest.raises(AuthError) as err:
        run(tmp_path, FakePost({}), lambda seen: {"error": "access_denied", "state": seen["state"]})
    assert err.value.code == "auth.denied"


def test_other_data_center_is_reported_before_any_exchange(tmp_path):
    post = FakePost({"refresh_token": "never"})
    with pytest.raises(ConfigError) as err:
        run(tmp_path, post, lambda seen: {**good(seen), "location": "eu"})
    assert err.value.code == "config.dc_mismatch"
    assert post.calls == []


def test_response_without_refresh_token_is_an_error_that_does_not_echo_secrets(tmp_path):
    with pytest.raises(AuthError) as err:
        run(tmp_path, FakePost({"error": "invalid_code"}), good)
    assert err.value.code == "auth.exchange_failed"
    assert "csecret-456" not in err.value.message + (err.value.hint or "")


def test_unreachable_accounts_server_is_an_upstream_error(tmp_path):
    with pytest.raises(UpstreamError):
        run(tmp_path, FakePost(ConnectionError("down")), good)


def test_missing_client_credentials_fail_before_listening(tmp_path):
    with pytest.raises(AuthError) as err:
        authorize(make_settings(tmp_path), FakePost({}), announce=lambda _l: None)
    assert err.value.code == "auth.missing"


def test_busy_port_is_a_usage_error(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        with pytest.raises(UsageError) as err:
            authorize(make_settings(tmp_path, **CLIENT), FakePost({}), port=s.getsockname()[1], announce=lambda _l: None)
    assert err.value.code == "usage.port_unavailable"


def test_no_redirect_times_out(tmp_path):
    ticks = iter(range(0, 1000, 5))
    with pytest.raises(AuthError) as err:
        authorize(make_settings(tmp_path, **CLIENT), FakePost({}), port=free_port(), announce=lambda _l: None,
                  timeout=10, clock=lambda: next(ticks))
    assert err.value.code == "auth.login_timeout"


def test_unrelated_requests_to_the_listener_do_not_end_the_wait(tmp_path):
    def probe_then_callback(seen):
        base = seen["redirect_uri"].rsplit("/", 1)[0]
        try:
            urllib.request.urlopen(base + "/favicon.ico", timeout=5)
        except Exception:
            pass
        return good(seen)

    token, _ = run(tmp_path, FakePost({"refresh_token": "1000.ok-token"}), probe_then_callback)
    assert token == "1000.ok-token"


# --- saving the token -------------------------------------------------------------------------------

def test_set_refresh_token_keeps_the_rest_of_the_file_and_the_mode(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('# my config\nclient_id = "cid"  # keep\nrefresh_token = "old-token-1"\ndc = "eu"\n')
    set_refresh_token(path, "1000.new-token-xyz")
    assert path.read_text() == '# my config\nclient_id = "cid"  # keep\nrefresh_token = "1000.new-token-xyz"\ndc = "eu"\n'
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_set_refresh_token_creates_a_private_config(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    set_refresh_token(path, "1000.new-token-xyz")
    assert 'refresh_token = "1000.new-token-xyz"' in path.read_text()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_set_refresh_token_refuses_an_odd_token_without_echoing_it(tmp_path):
    with pytest.raises(ConfigError) as err:
        set_refresh_token(tmp_path / "config.toml", 'bad"\ntoken')
    assert "bad" not in err.value.message
