import os

import pytest

from zoho_projects_axi.core.config import load_settings


def write_config(xdg_home, text, mode=0o600):
    path = xdg_home / "zoho-projects-axi" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    os.chmod(path, mode)
    return path


def load(tmp_path, env=None, **kwargs):
    base_env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "home")}
    base_env.update(env or {})
    kwargs.setdefault("legacy_env_file", tmp_path / "no-such.env")
    return load_settings(env=base_env, **kwargs)


def test_defaults_when_nothing_is_configured(tmp_path):
    s = load(tmp_path)
    assert s.client_id is None and s.client_secret is None and s.refresh_token is None
    assert s.dc == "com"
    assert s.max_images == 5
    assert s.max_attachment_bytes == 8 * 1024 * 1024
    assert s.sources["dc"] == "default"
    assert s.warnings == []


def test_reads_values_from_config_file_under_xdg_home(tmp_path):
    write_config(tmp_path / "xdg", 'client_id = "cid"\nrefresh_token = "rt"\ndc = "eu"\nportal_id = "p1"\nproject_id = "pr1"\n')
    s = load(tmp_path)
    assert (s.client_id, s.refresh_token, s.dc) == ("cid", "rt", "eu")
    assert (s.portal_id, s.project_id) == ("p1", "pr1")
    assert s.sources["client_id"] == "file"


def test_config_dir_falls_back_to_home_dot_config(tmp_path):
    path = tmp_path / "home" / ".config" / "zoho-projects-axi" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('client_id = "from-home"\n')
    os.chmod(path, 0o600)
    s = load_settings(env={"HOME": str(tmp_path / "home")}, legacy_env_file=tmp_path / "none.env")
    assert s.client_id == "from-home"


def test_env_overrides_file(tmp_path):
    write_config(tmp_path / "xdg", 'client_id = "from-file"\ndc = "eu"\n')
    s = load(tmp_path, env={"ZOHO_PROJECTS_AXI_CLIENT_ID": "from-env"})
    assert s.client_id == "from-env"
    assert s.sources["client_id"] == "env"
    assert s.dc == "eu"  # untouched keys still come from file


def test_explicit_override_beats_env_and_file(tmp_path):
    write_config(tmp_path / "xdg", 'project_id = "file-proj"\n')
    s = load(tmp_path, env={"ZOHO_PROJECTS_AXI_PROJECT_ID": "env-proj"}, overrides={"project_id": "flag-proj"})
    assert s.project_id == "flag-proj"
    assert s.sources["project_id"] == "flag"


def test_numeric_settings_are_parsed_from_env(tmp_path):
    s = load(tmp_path, env={"ZOHO_PROJECTS_AXI_MAX_IMAGES": "2", "ZOHO_PROJECTS_AXI_MAX_ATTACHMENT_BYTES": "1024"})
    assert (s.max_images, s.max_attachment_bytes) == (2, 1024)


def test_legacy_env_vars_are_a_fallback_with_exactly_one_warning(tmp_path):
    s = load(tmp_path, env={
        "ZOHO_CLIENT_ID": "legacy-id",
        "ZOHO_CLIENT_SECRET": "legacy-secret",
        "ZOHO_MCP_MAX_CONTEXT_IMAGES": "3",
    })
    assert (s.client_id, s.client_secret, s.max_images) == ("legacy-id", "legacy-secret", 3)
    assert s.sources["client_id"] == "legacy-env"
    assert len(s.warnings) == 1
    assert "deprecated" in s.warnings[0].lower()


def test_file_value_beats_legacy_value_and_emits_no_warning(tmp_path):
    write_config(tmp_path / "xdg", 'client_id = "from-file"\n')
    s = load(tmp_path, env={"ZOHO_CLIENT_ID": "legacy-id"})
    assert s.client_id == "from-file"
    assert s.warnings == []  # legacy value was ignored, so nothing deprecated was used


def test_legacy_dotenv_file_is_read_without_touching_process_env(tmp_path):
    dotenv = tmp_path / "legacy.env"
    dotenv.write_text("ZOHO_REFRESH_TOKEN=legacy-rt\nZOHO_PORTAL_ID=lp\n")
    before = dict(os.environ)
    s = load(tmp_path, legacy_env_file=dotenv)
    assert (s.refresh_token, s.portal_id) == ("legacy-rt", "lp")
    assert s.sources["refresh_token"] == "legacy-dotenv"
    assert len(s.warnings) == 1
    assert dict(os.environ) == before


def test_multiple_legacy_sources_still_produce_one_warning(tmp_path):
    dotenv = tmp_path / "legacy.env"
    dotenv.write_text("ZOHO_REFRESH_TOKEN=legacy-rt\n")
    s = load(tmp_path, env={"ZOHO_CLIENT_ID": "legacy-id"}, legacy_env_file=dotenv)
    assert len(s.warnings) == 1


@pytest.mark.parametrize("dc", ["com", "eu", "in", "com.au", "jp"])
def test_endpoint_bases_follow_data_center(tmp_path, dc):
    s = load(tmp_path, env={"ZOHO_PROJECTS_AXI_DC": dc})
    assert s.accounts_base == f"https://accounts.zoho.{dc}"
    assert s.api_v3_base == f"https://projectsapi.zoho.{dc}/api/v3"
    assert s.restapi_base == f"https://projectsapi.zoho.{dc}/restapi"


# --- the names before the rename to zoho-projects-axi -------------------------------------------------------

def test_the_old_env_prefix_still_works_as_a_deprecated_fallback(tmp_path):
    s = load_settings(env={"HOME": str(tmp_path), "ZOHO_AXI_CLIENT_ID": "old-id", "ZOHO_AXI_DC": "eu"},
                      legacy_env_file=tmp_path / "none.env")
    assert (s.client_id, s.dc) == ("old-id", "eu")
    assert s.sources["client_id"] == "legacy-env"
    assert any("deprecated" in w for w in s.warnings)


def test_the_new_env_prefix_beats_the_old_one(tmp_path):
    s = load_settings(env={"HOME": str(tmp_path), "ZOHO_AXI_CLIENT_ID": "old", "ZOHO_PROJECTS_AXI_CLIENT_ID": "new"},
                      legacy_env_file=tmp_path / "none.env")
    assert s.client_id == "new"


def test_a_config_at_the_old_location_is_read_with_a_move_warning(tmp_path):
    old = tmp_path / ".config" / "zoho-axi" / "config.toml"
    old.parent.mkdir(parents=True)
    old.write_text('client_id = "from-old-file"\n')
    old.chmod(0o600)
    s = load_settings(env={"HOME": str(tmp_path)}, legacy_env_file=tmp_path / "none.env")
    assert s.client_id == "from-old-file" and s.config_path == old
    assert any(str(tmp_path / ".config" / "zoho-projects-axi" / "config.toml") in w for w in s.warnings)


def test_the_new_config_location_wins_when_both_exist(tmp_path):
    for name, cid in (("zoho-axi", "old"), ("zoho-projects-axi", "new")):
        path = tmp_path / ".config" / name / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text(f'client_id = "{cid}"\n')
        path.chmod(0o600)
    s = load_settings(env={"HOME": str(tmp_path)}, legacy_env_file=tmp_path / "none.env")
    assert s.client_id == "new"
