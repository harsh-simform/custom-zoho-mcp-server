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


# --- review fixes: data center allowlist, empty values, numeric validation, legacy path ---

@pytest.mark.parametrize("bad_dc", ["com.evil.test", "com/x#", "ca", "COM"])
def test_unknown_data_center_is_rejected_so_credentials_cannot_go_to_arbitrary_hosts(tmp_path, bad_dc):
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, env={"ZOHO_AXI_DC": bad_dc})
    assert exc.value.code == "config.invalid"
    assert "dc" in exc.value.message
    assert "eu" in exc.value.hint  # the allowed values are listed


def test_unknown_data_center_in_the_config_file_is_rejected_too(tmp_path):
    write_config(tmp_path, 'dc = "evil.test"\n')
    with pytest.raises(ConfigError):
        load(tmp_path)


def test_empty_file_value_counts_as_unset_so_a_lower_layer_can_supply_it(tmp_path):
    write_config(tmp_path, 'client_id = ""\n')
    s = load(tmp_path, env={"ZOHO_CLIENT_ID": "legacy-id"})
    assert s.client_id == "legacy-id"
    assert s.sources["client_id"] == "legacy-env"


@pytest.mark.parametrize(
    "toml_line",
    ["max_images = -1", "max_attachment_bytes = 0", "ttl_task_seconds = -5", "max_images = 1.9", "max_images = true"],
)
def test_numeric_settings_must_be_whole_numbers_in_range(tmp_path, toml_line):
    write_config(tmp_path, toml_line + "\n")
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    assert exc.value.code == "config.invalid"


def test_zero_is_allowed_where_it_means_off(tmp_path):
    write_config(tmp_path, "max_images = 0\nttl_task_seconds = 0\n")
    s = load(tmp_path)
    assert (s.max_images, s.ttl_task_seconds) == (0, 0)


def test_default_legacy_dotenv_is_only_read_from_a_source_checkout(tmp_path, monkeypatch):
    import zoho_axi.core.config as config

    fake_root = tmp_path / "site-packages-parent"
    fake_root.mkdir()
    (fake_root / ".env").write_text("ZOHO_CLIENT_ID=stray\n")
    monkeypatch.setattr(config, "_REPO_ROOT", fake_root)

    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "home")}
    assert config.load_settings(env=env).client_id is None  # no pyproject.toml next to it: not a checkout

    (fake_root / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert config.load_settings(env=env).client_id == "stray"
