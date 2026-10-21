"""T14: the packaged skill file (lint) and `skill install` at the product seam, against a temp HOME."""
import io
import re
from importlib import resources

import pytest
from support.safe_home import real_home_is_off_limits  # noqa: F401  (autouse fixture)

from zoho_axi.cli.main import main

# The command names the spec's CLI surface (00-overview.md, "CLI surface" and D10) allows the skill to mention.
SPEC_COMMANDS = {"home", "auth", "project", "tasks", "task", "bug", "people", "resolve", "cache", "hooks",
                 "skill", "session-start"}


def packaged() -> str:
    return resources.files("zoho_axi").joinpath("skills/zoho-axi/SKILL.md").read_text(encoding="utf-8")


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def run(home, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), {"HOME": str(home), "ZOHO_MCP_ENV_FILE": str(home / "none.env")}, out, err)
    return code, out.getvalue(), err.getvalue()


def installed(home):
    return home / ".claude" / "skills" / "zoho-axi" / "SKILL.md"


# --- the file itself ---------------------------------------------------------------------------

def front_matter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "SKILL.md must start with a --- front-matter block"
    return dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)


def test_front_matter_has_name_and_description():
    meta = front_matter(packaged())
    assert meta["name"].strip() == "zoho-axi"
    assert len(meta["description"].strip()) > 40


def test_the_skill_is_short():
    assert len(packaged().splitlines()) < 120


def test_it_tells_the_agent_to_start_with_resolve_and_to_use_help_for_flags():
    text = packaged()
    assert re.search(r"start with `?(zoho-axi )?resolve", text, re.IGNORECASE)
    assert "--help" in text


def test_it_does_not_duplicate_a_flag_reference():
    flags = set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", packaged()))
    # the slice flags and the few global ones the guidance needs; a per-command flag table would blow past this
    assert len(flags) <= 14, sorted(flags)


def test_it_only_mentions_commands_the_cli_has_or_will_have():
    named = set(re.findall(r"`zoho-axi ([a-z][a-z-]*)", packaged()))
    assert named, "the skill should show command forms"
    assert named <= SPEC_COMMANDS, sorted(named - SPEC_COMMANDS)


# --- skill install -----------------------------------------------------------------------------

def test_install_without_yes_shows_what_it_would_do_and_writes_nothing(home):
    code, out, err = run(home, "skill", "install")
    assert code == 0 and err == ""
    assert "status: dry run" in out and "change: create" in out
    assert "zoho-axi skill install --yes" in out
    assert list(home.iterdir()) == []


def test_install_yes_copies_the_packaged_skill(home):
    code, out, _ = run(home, "skill", "install", "--yes")
    assert code == 0 and "status: installed" in out
    assert installed(home).read_text(encoding="utf-8") == packaged()


def test_a_second_install_is_a_no_op(home):
    run(home, "skill", "install", "--yes")
    before = installed(home).stat().st_mtime_ns
    code, out, _ = run(home, "skill", "install", "--yes")
    assert code == 0 and "status: up to date" in out
    assert installed(home).stat().st_mtime_ns == before
    assert not installed(home).with_name("SKILL.md.bak").exists()


def test_a_changed_installed_copy_is_shown_as_a_diff_and_only_replaced_with_yes(home):
    run(home, "skill", "install", "--yes")
    installed(home).write_text("---\nname: zoho-axi\ndescription: my edited copy\n---\nlocal notes\n")
    edited = installed(home).read_text()
    code, out, _ = run(home, "skill", "install")
    assert code == 0 and "change: update" in out
    assert "diff[" not in out  # a real unified diff on stdout, not TOON rows
    assert "-description: my edited copy" in out.splitlines()  # the user's line is what the install would remove
    assert installed(home).read_text() == edited
    code, out, _ = run(home, "skill", "install", "--yes")
    assert "status: installed" in out
    assert installed(home).read_text(encoding="utf-8") == packaged()
    assert installed(home).with_name("SKILL.md.bak").read_text() == edited


def test_a_second_replacement_never_overwrites_the_first_backup(home):
    run(home, "skill", "install", "--yes")
    installed(home).write_text("first local edit\n")
    run(home, "skill", "install", "--yes")
    installed(home).write_text("second local edit\n")
    code, out, _ = run(home, "skill", "install", "--yes")
    assert code == 0 and "status: installed" in out
    assert installed(home).with_name("SKILL.md.bak").read_text() == "first local edit\n"
    assert installed(home).with_name("SKILL.md.bak.1").read_text() == "second local edit\n"
    assert f"backup: {installed(home).with_name('SKILL.md.bak.1')}" in out


def test_install_refuses_a_target_outside_the_injected_home(tmp_path, home):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / ".claude").symlink_to(elsewhere)
    code, out, _ = run(home, "skill", "install", "--yes")
    assert code == 2 and out.splitlines()[0] == "error: usage.unsafe_target"
    assert list(elsewhere.iterdir()) == []


def test_the_skill_ships_as_package_data():
    """pyproject must declare the file as package data, or a built wheel would not contain it (checked by
    building one and installing it into a fresh venv; see the T14 report)."""
    import fnmatch
    from pathlib import Path

    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib

    root = Path(__file__).resolve().parent.parent
    config = tomllib.loads((root / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["zoho_axi"]
    relative = "skills/zoho-axi/SKILL.md"
    assert (root / "zoho_axi" / relative).is_file()
    assert any(fnmatch.fnmatch(relative, pattern) for pattern in patterns), patterns
