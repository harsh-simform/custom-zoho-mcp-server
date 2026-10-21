"""T15: `config.example.toml` stays in step with config.py. It must parse, document every setting the loader
knows (live or as a commented `# key = value` line), contain no key the loader does not know, and every value it
shows must be one the loader accepts (checked by loading it with all the commented lines switched on)."""
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from zoho_axi.core.config import _SPEC, load_settings

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.toml"
COMMENTED_SETTING = re.compile(r"^#\s*([a-z_]+)\s*=\s*(.+)$", re.M)


def live_keys():
    return set(tomllib.loads(EXAMPLE.read_text()))


def commented_keys():
    return {m.group(1) for m in COMMENTED_SETTING.finditer(EXAMPLE.read_text())}


def test_the_example_parses():
    assert live_keys()


def test_every_key_in_the_example_is_one_config_py_knows():
    assert live_keys() <= set(_SPEC)
    assert commented_keys() <= set(_SPEC)


def test_every_setting_config_py_knows_is_documented_in_the_example():
    assert live_keys() | commented_keys() == set(_SPEC)


def test_the_credentials_are_the_live_lines_and_everything_else_is_optional():
    assert live_keys() == {"client_id", "client_secret", "refresh_token"}


def test_the_example_loads_as_is_and_with_every_optional_line_enabled(tmp_path):
    text = EXAMPLE.read_text()
    enabled = COMMENTED_SETTING.sub(lambda m: f"{m.group(1)} = {m.group(2)}", text)
    loaded = {}
    for name, body in (("as-is", text), ("all-on", enabled)):
        path = tmp_path / f"{name}.toml"
        path.write_text(body)
        path.chmod(0o600)
        loaded[name] = load_settings(env={"HOME": str(tmp_path)}, config_path=path, legacy_env_file=tmp_path / "none.env")
        assert loaded[name].client_id and loaded[name].dc == "com" and not loaded[name].warnings, name
    assert loaded["all-on"].max_images == 5 and loaded["all-on"].portal_id == "1100000000001"


def test_the_example_states_the_mode_600_rule_and_the_legacy_deprecation():
    text = EXAMPLE.read_text()
    assert "chmod 600" in text and "mode 600" in text
    assert "DEPRECATED" in text and ".env" in text and "ZOHO_AXI_<KEY>" in text


def test_the_old_env_example_is_gone():
    assert not (ROOT / ".env.example").exists()
