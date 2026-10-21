import os

import pytest

from zoho_axi.core.config import load_settings, resolve_project
from zoho_axi.core.errors import AuthError, ConfigError


def write_config(tmp_path, text, mode=0o600):
    path = tmp_path / "xdg" / "zoho-axi" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    os.chmod(path, mode)
    return path


def load(tmp_path, env=None, **kwargs):
    base_env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "home")}
    base_env.update(env or {})
    return load_settings(env=base_env, legacy_env_file=tmp_path / "none.env", **kwargs)


# --- permissions ---

def test_loose_config_permissions_produce_a_warning_naming_the_file(tmp_path):
    path = write_config(tmp_path, 'client_id = "x"\n', mode=0o644)
    s = load(tmp_path)
    assert any(str(path) in w and "600" in w for w in s.warnings)


def test_strict_mode_rejects_loose_config_permissions(tmp_path):
    write_config(tmp_path, 'client_id = "x"\n', mode=0o640)
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, strict=True)
    assert exc.value.code == "config.permissions"
    assert exc.value.exit_code == 3


def test_owner_only_permissions_are_silent(tmp_path):
    write_config(tmp_path, 'client_id = "x"\n', mode=0o600)
    assert load(tmp_path, strict=True).warnings == []


# --- invalid input ---

def test_non_numeric_value_for_numeric_setting_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, env={"ZOHO_AXI_MAX_IMAGES": "lots"})
    assert exc.value.code == "config.invalid"
    assert "max_images" in exc.value.message


def test_malformed_toml_is_a_config_error_naming_the_file(tmp_path):
    path = write_config(tmp_path, "client_id = = oops\n")
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    assert exc.value.code == "config.invalid"
    assert str(path) in exc.value.message


# --- credentials ---

def test_missing_credentials_raise_auth_missing_naming_file_and_keys(tmp_path):
    s = load(tmp_path, env={"ZOHO_AXI_CLIENT_ID": "id-only"})
    with pytest.raises(AuthError) as exc:
        s.require_credentials()
    err = exc.value
    assert err.code == "auth.missing"
    assert err.exit_code == 3
    assert str(s.config_path) in err.hint
    assert "client_secret" in err.hint and "refresh_token" in err.hint
    listed = err.hint.split("missing:")[1].split(".")[0]
    assert "client_id" not in listed  # only the absent keys are listed


def test_complete_credentials_are_returned(tmp_path):
    s = load(tmp_path, env={
        "ZOHO_AXI_CLIENT_ID": "i", "ZOHO_AXI_CLIENT_SECRET": "s", "ZOHO_AXI_REFRESH_TOKEN": "r",
    })
    assert s.require_credentials() == ("i", "s", "r")


# --- project resolution ---

def test_project_flag_beats_config_default(tmp_path):
    s = load(tmp_path, env={"ZOHO_AXI_PORTAL_ID": "p1", "ZOHO_AXI_PROJECT_ID": "default"})
    assert resolve_project(s, "other") == ("p1", "other")


def test_project_flag_can_carry_its_own_portal(tmp_path):
    s = load(tmp_path, env={"ZOHO_AXI_PORTAL_ID": "p1"})
    assert resolve_project(s, "p2:pr9") == ("p2", "pr9")


def test_config_default_is_used_without_a_flag(tmp_path):
    s = load(tmp_path, env={"ZOHO_AXI_PORTAL_ID": "p1", "ZOHO_AXI_PROJECT_ID": "pr1"})
    assert resolve_project(s) == ("p1", "pr1")


def test_no_project_anywhere_is_an_error_with_a_hint(tmp_path):
    s = load(tmp_path)
    with pytest.raises(ConfigError) as exc:
        resolve_project(s)
    assert exc.value.code == "config.project_missing"
    assert exc.value.exit_code == 3
    assert "project_id" in exc.value.hint
