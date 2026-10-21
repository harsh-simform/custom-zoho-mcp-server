import json

from zoho_projects_axi.core.auth import ZohoAuth
from zoho_projects_axi.core.config import load_settings
from zoho_projects_axi.core.status import auth_status, login_help

from test_auth import SECRETS, FakePost, make_settings


def test_offline_status_reports_unverified_and_makes_no_network_call(tmp_path):
    post = FakePost()
    settings = make_settings(tmp_path)
    result = auth_status(settings, auth=ZohoAuth(settings, post=post), online=False)
    assert result["credentials"] == "complete"
    assert result["token"] == "unverified"
    assert post.calls == []


def test_online_status_reports_a_working_token_as_valid(tmp_path):
    settings = make_settings(tmp_path)
    auth = ZohoAuth(settings, post=FakePost({"access_token": "t", "expires_in": 3600}))
    assert auth_status(settings, auth=auth)["token"] == "valid"


def test_online_status_reports_rejected_credentials_as_invalid_with_the_error_code(tmp_path):
    settings = make_settings(tmp_path)
    auth = ZohoAuth(settings, post=FakePost({"error": "invalid_client"}))
    result = auth_status(settings, auth=auth)
    assert result["token"] == "invalid"
    assert result["token_error"] == "auth.refresh_failed"


def test_online_status_reports_network_failure_as_unreachable(tmp_path):
    settings = make_settings(tmp_path)
    auth = ZohoAuth(settings, post=FakePost(ConnectionError("down")))
    result = auth_status(settings, auth=auth)
    assert result["token"] == "unreachable"
    assert result["token_error"] == "upstream.unreachable"


def test_missing_credentials_are_listed_and_never_trigger_a_network_call(tmp_path):
    settings = load_settings(
        env={"HOME": str(tmp_path), "ZOHO_PROJECTS_AXI_CLIENT_ID": "only-id"}, legacy_env_file=tmp_path / "none.env"
    )
    post = FakePost()
    result = auth_status(settings, auth=ZohoAuth(settings, post=post))
    assert result["credentials"] == "missing: client_secret, refresh_token"
    assert result["token"] == "unverified"
    assert post.calls == []


def test_status_shows_where_each_setting_came_from(tmp_path):
    result = auth_status(make_settings(tmp_path, ZOHO_PROJECTS_AXI_DC="eu"), online=False)
    assert result["settings"]["client_secret"] == "env"
    assert result["settings"]["dc"] == "env"
    assert result["settings"]["portal_id"] == "unset"
    assert result["config_file"].endswith("zoho-projects-axi/config.toml")
    assert result["config_file_exists"] is False


def test_status_never_contains_secret_values(tmp_path):
    settings = make_settings(tmp_path)
    auth = ZohoAuth(settings, post=FakePost({"access_token": "tok-secret-xyz", "expires_in": 3600}))
    rendered = json.dumps(auth_status(settings, auth=auth))
    for value in [*SECRETS.values(), "tok-secret-xyz"]:
        assert value not in rendered


def test_login_help_lists_the_self_client_steps_and_the_config_path(tmp_path):
    settings = make_settings(tmp_path)
    text = login_help(settings)
    assert "api-console.zoho.com" in text
    assert "ZohoProjects.search.READ" in text
    assert str(settings.config_path) in text
    assert "refresh_token" in text


def test_unexpected_exceptions_are_reported_not_raised(tmp_path):
    settings = make_settings(tmp_path)
    auth = ZohoAuth(settings, post=FakePost(ValueError("weird")))
    result = auth_status(settings, auth=auth)
    assert result["token"] == "unverified"
    assert result["token_error"] == "ValueError"


def test_login_help_points_at_the_console_for_the_configured_data_center(tmp_path):
    text = login_help(make_settings(tmp_path, ZOHO_PROJECTS_AXI_DC="eu"))
    assert "https://api-console.zoho.eu" in text
    assert "api-console.zoho.com" not in text
