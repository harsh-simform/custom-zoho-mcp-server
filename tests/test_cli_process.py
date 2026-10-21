"""T06 process-level tests: the installed `zoho-axi` / `zoho` entry points run as real subprocesses.

They need the package installed into the interpreter running the tests (`pip install -e .`), which is
the same thing `uv tool install .` produces. stdin is closed (not just empty) in every run: the CLI must
never read from a terminal, so a closed descriptor 0 must not change anything.
"""
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(sys.executable).parent


def run_installed(command, args, tmp_path, stdin_closed=True):
    exe = BIN / command
    assert exe.exists(), f"{exe} missing: install the package with `pip install -e .`"
    env = {"HOME": str(tmp_path), "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env"), "PATH": "/usr/bin:/bin"}
    if stdin_closed:  # `<&-` closes fd 0 before exec, so any read from stdin would raise
        cmd = ["/bin/sh", "-c", f'exec "$0" "$@" <&-', str(exe), *args]
    else:
        cmd = [str(exe), *args]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30, cwd=tmp_path)


@pytest.mark.parametrize("command", ["zoho-axi", "zoho"])
def test_bare_command_shows_home_with_stdin_closed(command, tmp_path):
    done = run_installed(command, [], tmp_path)
    assert done.returncode == 0
    assert done.stdout.startswith("portal: unset\n")
    assert done.stderr == ""


# With no config and no credentials, the commands that need Zoho end in a structured config/auth error
# (exit 3) and `project use` / `cache clear` in a usage error (exit 2: no argument / no --yes); every
# other command works offline. What matters for this test is that none of them reads stdin or crashes.
EXPECTED_WITHOUT_CONFIG = {"project use": 2, "cache clear": 2, "project": 3, "project list": 3, "people": 3, "tasks": 3, "task": 2, "bug": 2, "resolve": 2}


def test_every_wired_command_runs_with_stdin_closed(tmp_path):
    from zoho_axi.cli.registry import REGISTRY

    for spec in REGISTRY:
        args = [*spec.name.split(), "--offline"] if spec.name == "auth status" else spec.name.split()
        done = run_installed("zoho-axi", args, tmp_path)
        expected = EXPECTED_WITHOUT_CONFIG.get(spec.name, 0)
        assert done.returncode == expected, (spec.name, done.stdout, done.stderr)
        assert (done.stdout.startswith("error: ") if expected else not done.stdout.startswith("error: ")), spec.name
        assert done.stderr == ""


def test_unknown_flag_exits_2_with_the_error_on_stdout(tmp_path):
    done = run_installed("zoho-axi", ["auth", "status", "--offlin"], tmp_path)
    assert done.returncode == 2
    assert done.stdout.startswith("error: usage.unknown_flag\n")
    assert "Did you mean --offline?" in done.stdout
    assert done.stderr == ""


def test_help_exits_0_with_stdin_closed(tmp_path):
    done = run_installed("zoho-axi", ["--help"], tmp_path)
    assert done.returncode == 0
    assert "auth status" in done.stdout


def test_importing_the_cli_has_no_side_effects(tmp_path):
    script = (
        "import sys\n"
        "import zoho_axi.cli.main, zoho_axi.cli.registry, zoho_axi.cli.parser, zoho_axi.cli.help\n"
        "assert 'requests' not in sys.modules\n"
        "print('ok')\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", script], env={"HOME": str(tmp_path), "PATH": ""},
                          capture_output=True, text=True, cwd=tmp_path)
    assert done.stdout.strip() == "ok", done.stderr
    assert list(tmp_path.iterdir()) == []


def test_legacy_shim_prints_a_one_line_deprecation_note_to_stderr_and_still_starts_the_server(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "", "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env")}
    done = subprocess.run([sys.executable, "-m", "zoho_mcp"], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""  # stdout is the MCP protocol channel
    notes = [line for line in done.stderr.splitlines() if "deprecated" in line]
    assert len(notes) == 1 and "zoho-axi" in notes[0]
    assert "MCP server starting" in done.stderr  # the legacy server still ran


# --- resolve (T11): the one command that may read stdin -------------------------------------------------

def test_resolve_reads_a_real_pipe_when_the_text_is_dash(tmp_path):
    env = {"HOME": str(tmp_path), "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env"), "PATH": "/usr/bin:/bin"}
    done = subprocess.run([str(BIN / "zoho-axi"), "resolve", "-"], input="see the comments on AD1-T123", env=env,
                          capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert done.returncode == 0 and done.stderr == ""
    assert done.stdout.splitlines()[-2:] == ["help[1]{run}:", "  zoho-axi task AD1-T123 --comments"]


def test_resolve_dash_with_stdin_closed_is_a_usage_error_not_a_hang_or_crash(tmp_path):
    done = run_installed("zoho-axi", ["resolve", "-"], tmp_path)
    assert done.returncode == 2 and done.stderr == ""
    assert done.stdout.startswith("error: usage.stdin_unavailable\n")


def test_resolve_with_text_in_argv_works_offline_with_stdin_closed(tmp_path):
    done = run_installed("zoho-axi", ["resolve", "AD1-B45"], tmp_path)
    assert done.returncode == 0 and done.stderr == ""
    assert done.stdout.splitlines()[:2] == ["found[1]{key,type,resolved}:", "  AD1-B45,bug,no"]
