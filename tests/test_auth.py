import logging

import pytest

from zoho_projects_axi.core.auth import ZohoAuth
from zoho_projects_axi.core.config import load_settings
from zoho_projects_axi.core.errors import AuthError, RateLimitedError, UpstreamError

SECRETS = {"id": "cid-123", "secret": "super-secret-value", "refresh": "refresh-token-value"}


def make_settings(tmp_path, **env_extra):
    env = {
        "HOME": str(tmp_path),
        "ZOHO_PROJECTS_AXI_CLIENT_ID": SECRETS["id"],
        "ZOHO_PROJECTS_AXI_CLIENT_SECRET": SECRETS["secret"],
        "ZOHO_PROJECTS_AXI_REFRESH_TOKEN": SECRETS["refresh"],
    }
    env.update(env_extra)
    return load_settings(env=env, legacy_env_file=tmp_path / "none.env")


class FakePost:
    """Stands in for the HTTP call to Zoho's token endpoint."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, data, timeout):
        self.calls.append((url, dict(data), timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_refresh_uses_refresh_token_grant_against_the_data_center_endpoint(tmp_path):
    post = FakePost({"access_token": "tok-1", "expires_in": 3600})
    auth = ZohoAuth(make_settings(tmp_path, ZOHO_PROJECTS_AXI_DC="eu"), post=post)

    assert auth.get_access_token() == "tok-1"

    url, data, _ = post.calls[0]
    assert url == "https://accounts.zoho.eu/oauth/v2/token"
    assert data == {
        "grant_type": "refresh_token",
        "client_id": SECRETS["id"],
        "client_secret": SECRETS["secret"],
        "refresh_token": SECRETS["refresh"],
    }


def test_token_is_reused_until_shortly_before_it_expires(tmp_path):
    post = FakePost({"access_token": "tok-1", "expires_in": 3600}, {"access_token": "tok-2", "expires_in": 3600})
    clock = Clock()
    auth = ZohoAuth(make_settings(tmp_path), post=post, clock=clock)

    assert auth.get_access_token() == "tok-1"
    clock.now += 3000
    assert auth.get_access_token() == "tok-1"
    assert len(post.calls) == 1

    clock.now += 600  # now inside the 60s safety margin before expiry
    assert auth.get_access_token() == "tok-2"
    assert len(post.calls) == 2


def test_zoho_error_response_becomes_auth_refresh_failed(tmp_path):
    auth = ZohoAuth(make_settings(tmp_path), post=FakePost({"error": "invalid_client"}))
    with pytest.raises(AuthError) as exc:
        auth.get_access_token()
    assert exc.value.code == "auth.refresh_failed"
    assert exc.value.exit_code == 3
    assert "invalid_client" in exc.value.message


def test_missing_credentials_fail_before_any_network_call(tmp_path):
    settings = load_settings(env={"HOME": str(tmp_path)}, legacy_env_file=tmp_path / "none.env")
    post = FakePost()
    with pytest.raises(AuthError) as exc:
        ZohoAuth(settings, post=post).get_access_token()
    assert exc.value.code == "auth.missing"
    assert post.calls == []


def test_network_failure_is_an_upstream_error_not_an_auth_error(tmp_path):
    auth = ZohoAuth(make_settings(tmp_path), post=FakePost(ConnectionError("dns down")))
    with pytest.raises(UpstreamError) as exc:
        auth.get_access_token()
    assert exc.value.code == "upstream.unreachable"
    assert exc.value.exit_code == 5


def test_validate_forces_a_fresh_refresh(tmp_path):
    post = FakePost({"access_token": "a", "expires_in": 3600}, {"access_token": "b", "expires_in": 3600})
    auth = ZohoAuth(make_settings(tmp_path), post=post)
    auth.get_access_token()
    auth.validate()
    assert len(post.calls) == 2


# --- secret hygiene: nothing observable may contain a credential ---

def _assert_no_secrets(text):
    for value in SECRETS.values():
        assert value not in text


def test_secrets_never_reach_logs_output_or_repr_on_success_and_failure(tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    settings = make_settings(tmp_path)

    ok = ZohoAuth(settings, post=FakePost({"access_token": "tok-visible-ok", "expires_in": 3600}))
    ok.get_access_token()

    bad = ZohoAuth(settings, post=FakePost({"error": "invalid_code", "echo": SECRETS["secret"]}))
    with pytest.raises(AuthError) as exc:
        bad.get_access_token()

    _assert_no_secrets(caplog.text)
    out = capsys.readouterr()
    _assert_no_secrets(out.out + out.err)
    _assert_no_secrets(repr(settings))
    _assert_no_secrets(str(exc.value) + (exc.value.hint or ""))


def test_access_token_is_not_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    ZohoAuth(make_settings(tmp_path), post=FakePost({"access_token": "tok-do-not-log", "expires_in": 3600})).get_access_token()
    assert "tok-do-not-log" not in caplog.text


# --- review fixes: expires_in handling, concurrency, error formatting ---

def test_non_numeric_expires_in_falls_back_to_an_hour_instead_of_crashing(tmp_path):
    post = FakePost({"access_token": "t", "expires_in": "3600"})
    auth = ZohoAuth(make_settings(tmp_path), post=post, clock=Clock())
    assert auth.get_access_token() == "t"
    assert auth.get_access_token() == "t"
    assert len(post.calls) == 1


def test_very_short_lived_token_is_still_reused_within_the_same_instant(tmp_path):
    post = FakePost({"access_token": "t", "expires_in": 30}, {"access_token": "t2", "expires_in": 30})
    auth = ZohoAuth(make_settings(tmp_path), post=post, clock=Clock())
    auth.get_access_token()
    auth.get_access_token()
    assert len(post.calls) == 1


def test_concurrent_callers_trigger_a_single_refresh(tmp_path):
    import threading
    import time

    calls = []

    def slow_post(url, data, timeout):
        calls.append(1)
        time.sleep(0.05)
        return {"access_token": "t", "expires_in": 3600}

    auth = ZohoAuth(make_settings(tmp_path), post=slow_post)
    threads = [threading.Thread(target=auth.get_access_token) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1


def test_non_string_error_value_is_rendered_safely_and_hint_mentions_throttling(tmp_path):
    auth = ZohoAuth(make_settings(tmp_path), post=FakePost({"error": {"nested": "value"}}))
    with pytest.raises(AuthError) as exc:
        auth.get_access_token()
    assert exc.value.code == "auth.refresh_failed"
    assert "too many requests" in exc.value.hint.lower()


def test_zoho_throttle_reply_is_a_rate_limit_error_not_a_credential_problem(tmp_path):
    body = {"error": "Access Denied", "error_description": "You have made too many requests continuously. Try later."}
    auth = ZohoAuth(make_settings(tmp_path), post=FakePost(body))
    with pytest.raises(RateLimitedError) as exc:
        auth.get_access_token()
    assert exc.value.exit_code == 6
    assert exc.value.code == "rate_limited.token"
    assert "wait" in exc.value.hint.lower()
