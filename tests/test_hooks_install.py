"""T14: `hooks install` / `hooks uninstall` at the product seam (run command -> text + exit code), always
against a temp HOME. The real ~/.claude is unreachable (support/safe_home.py). Expected JSON documents
are literals written from Claude Code's documented SessionStart hook format
(https://code.claude.com/docs/en/hooks), not recomputed with the code's own helpers."""
import io
import json
import os
import subprocess
import sys

import pytest
from support.safe_home import real_home_is_off_limits  # noqa: F401  (autouse fixture)

from zoho_axi.cli.main import main

OURS = {"type": "command", "command": "zoho-axi session-start", "timeout": 10}
GROUP = {"matcher": "startup|resume|clear|compact", "hooks": [OURS]}

ORIGINAL = {
    "model": "opus",
    "permissions": {"allow": ["Bash(git status)"], "deny": []},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "check.sh"}]}],
        "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}],
    },
}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def run(home, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), {"HOME": str(home), "ZOHO_MCP_ENV_FILE": str(home / "none.env")}, out, err)
    return code, out.getvalue(), err.getvalue()


def settings_path(home):
    return home / ".claude" / "settings.json"


def write_settings(home, doc, indent=2):
    settings_path(home).parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=indent) + "\n"
    settings_path(home).write_text(text)
    return text


def read(home):
    return json.loads(settings_path(home).read_text())


def test_install_without_yes_prints_the_diff_and_changes_nothing(home):
    original = write_settings(home, ORIGINAL)
    code, out, err = run(home, "hooks", "install")
    assert code == 0 and err == ""
    assert settings_path(home).read_text() == original
    assert not (settings_path(home).parent / "settings.json.bak").exists()
    assert "status: dry run" in out
    assert "zoho-axi session-start" in out
    assert "zoho-axi hooks install --yes" in out  # the next step, as a hint


def test_install_without_yes_on_a_machine_without_claude_settings_creates_nothing(home):
    code, out, _ = run(home, "hooks", "install")
    assert code == 0 and "status: dry run" in out
    assert list(home.iterdir()) == []


def test_install_yes_adds_the_session_start_entry_and_keeps_everything_else(home):
    original = write_settings(home, ORIGINAL)
    code, out, err = run(home, "hooks", "install", "--yes")
    assert code == 0 and err == "" and "status: installed" in out
    expected = json.loads(original)
    expected["hooks"]["SessionStart"].append(GROUP)
    assert read(home) == expected
    assert (settings_path(home).parent / "settings.json.bak").read_text() == original


def test_install_yes_creates_the_file_when_missing_and_makes_no_backup(home):
    code, _, _ = run(home, "hooks", "install", "--yes")
    assert code == 0
    assert read(home) == {"hooks": {"SessionStart": [GROUP]}}
    assert not (settings_path(home).parent / "settings.json.bak").exists()


def test_install_keeps_the_files_indentation_and_trailing_newline(home):
    write_settings(home, {"model": "opus"}, indent=4)
    run(home, "hooks", "install", "--yes")
    text = settings_path(home).read_text()
    assert text.endswith("}\n")
    assert '\n    "model": "opus",\n    "hooks": {\n        "SessionStart": [' in text


def test_a_second_install_is_a_no_op_and_keeps_the_first_backup(home):
    original = write_settings(home, ORIGINAL)
    run(home, "hooks", "install", "--yes")
    installed = settings_path(home).read_text()
    code, out, _ = run(home, "hooks", "install", "--yes")
    assert code == 0 and "status: already installed" in out
    assert settings_path(home).read_text() == installed
    assert (settings_path(home).parent / "settings.json.bak").read_text() == original  # not overwritten by a no-op
    assert "already installed" in run(home, "hooks", "install")[1]  # the dry run agrees


def test_uninstall_restores_the_original_settings(home):
    write_settings(home, ORIGINAL)
    run(home, "hooks", "install", "--yes")
    code, out, _ = run(home, "hooks", "uninstall", "--yes")
    assert code == 0 and "status: removed" in out
    assert read(home) == ORIGINAL


def test_uninstall_after_a_fresh_install_leaves_an_empty_settings_object(home):
    run(home, "hooks", "install", "--yes")
    run(home, "hooks", "uninstall", "--yes")
    assert read(home) == {}


def test_uninstall_without_yes_changes_nothing(home):
    write_settings(home, ORIGINAL)
    run(home, "hooks", "install", "--yes")
    installed = settings_path(home).read_text()
    code, out, _ = run(home, "hooks", "uninstall")
    assert code == 0 and "status: dry run" in out and "hooks uninstall --yes" in out
    assert settings_path(home).read_text() == installed


def test_uninstall_when_not_installed_is_a_no_op_that_does_not_even_back_up(home):
    original = write_settings(home, ORIGINAL)
    code, out, _ = run(home, "hooks", "uninstall", "--yes")
    assert code == 0 and "status: not installed" in out
    assert settings_path(home).read_text() == original
    assert not (settings_path(home).parent / "settings.json.bak").exists()


def test_uninstall_leaves_other_hooks_of_the_user_in_the_same_group(home):
    doc = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}, OURS]}]}}
    write_settings(home, doc)
    run(home, "hooks", "uninstall", "--yes")
    assert read(home) == {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}]}}


def test_an_installed_entry_that_the_user_wrote_by_hand_counts_as_installed(home):
    doc = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "/opt/bin/zoho-axi session-start"}]}]}}
    original = write_settings(home, doc)
    code, out, _ = run(home, "hooks", "install", "--yes")
    assert code == 0 and "already installed" in out
    assert settings_path(home).read_text() == original


@pytest.mark.parametrize("content, error", [
    ("{not json", "config.invalid"),
    ("[1, 2]", "config.invalid"),
    ('{"hooks": []}', "config.unwritable"),
    ('{"hooks": {"SessionStart": {}}}', "config.unwritable"),
])
def test_unusable_settings_are_refused_and_left_alone(home, content, error):
    settings_path(home).parent.mkdir()
    settings_path(home).write_text(content)
    code, out, _ = run(home, "hooks", "install", "--yes")
    assert code == 3 and out.splitlines()[0] == f"error: {error}"
    assert settings_path(home).read_text() == content


def test_an_empty_settings_file_is_treated_as_an_empty_object(home):
    settings_path(home).parent.mkdir()
    settings_path(home).write_text("")
    assert run(home, "hooks", "install", "--yes")[0] == 0
    assert read(home) == {"hooks": {"SessionStart": [GROUP]}}


def test_the_file_mode_of_existing_settings_is_kept(home):
    write_settings(home, ORIGINAL)
    settings_path(home).chmod(0o600)
    run(home, "hooks", "install", "--yes")
    assert oct(settings_path(home).stat().st_mode & 0o777) == "0o600"


# --- safety: the target must stay inside the injected home ---------------------------------------

def test_a_claude_dir_that_resolves_outside_the_injected_home_is_refused(tmp_path, home):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / ".claude").symlink_to(elsewhere)
    for argv in (["hooks", "install", "--yes"], ["hooks", "uninstall", "--yes"], ["hooks", "install"]):
        code, out, _ = run(home, *argv)
        assert code == 2 and out.splitlines()[0] == "error: usage.unsafe_target", argv
    assert list(elsewhere.iterdir()) == []


def test_a_settings_file_symlinked_outside_the_injected_home_is_refused(tmp_path, home):
    outside = tmp_path / "outside.json"
    outside.write_text('{"model": "opus"}\n')
    (home / ".claude").mkdir()
    settings_path(home).symlink_to(outside)
    code, out, _ = run(home, "hooks", "install", "--yes")
    assert code == 2 and out.splitlines()[0] == "error: usage.unsafe_target"
    assert outside.read_text() == '{"model": "opus"}\n'


def test_a_symlinked_settings_file_that_stays_inside_home_is_written_through(home):
    dotfiles = home / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "settings.json"
    real.write_text('{"model": "opus"}\n')
    (home / ".claude").mkdir()
    settings_path(home).symlink_to(real)
    assert run(home, "hooks", "install", "--yes")[0] == 0
    assert settings_path(home).is_symlink()
    assert json.loads(real.read_text())["hooks"]["SessionStart"] == [GROUP]


def test_a_relative_home_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    code = main(["hooks", "install", "--yes"], {"HOME": "relative/home"}, out, err)
    assert code == 2 and out.getvalue().splitlines()[0] == "error: usage.unsafe_target"
    assert list(tmp_path.iterdir()) == []


def test_the_diff_keeps_the_indentation_of_the_json_lines(home):
    write_settings(home, {"model": "opus"})
    out = run(home, "hooks", "install")[1]
    assert '"+  \\"hooks\\": {"' in out  # TOON-quoted, but both leading spaces of the added line survive
    assert '"+    \\"SessionStart\\": ["' in out


def test_the_injected_home_is_used_not_the_process_home(home, real_home_is_off_limits):
    # real_home_is_off_limits points the process HOME at a decoy and fails if it is written to
    run(home, "hooks", "install", "--yes")
    assert settings_path(home).exists()
    assert list(real_home_is_off_limits.iterdir()) == []


def test_it_never_prompts_even_with_stdin_closed(tmp_path):
    h = tmp_path / "h"
    (h / ".claude").mkdir(parents=True)
    exe = os.path.join(os.path.dirname(sys.executable), "zoho-axi")
    done = subprocess.run(
        ["/bin/sh", "-c", 'exec "$0" "$@" <&-', exe, "hooks", "install"],
        env={"HOME": str(h), "PATH": "/usr/bin:/bin", "ZOHO_MCP_ENV_FILE": str(h / "none.env")},
        capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert done.returncode == 0 and done.stderr == "" and "status: dry run" in done.stdout
    assert list((h / ".claude").iterdir()) == []
