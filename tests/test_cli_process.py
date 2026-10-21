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


def test_every_wired_command_runs_with_stdin_closed(tmp_path):
    from zoho_axi.cli.registry import REGISTRY

    for spec in REGISTRY:
        args = [*spec.name.split(), "--offline"] if spec.name == "auth status" else spec.name.split()
        needs_arg = any(a.required for a in spec.args)
        args += ["1" for a in spec.args if a.required]  # e.g. `task 1`: unconfigured here, so a config error (3)
        done = run_installed("zoho-axi", args, tmp_path)
        assert done.returncode == (3 if needs_arg else 0), (spec.name, done.stdout, done.stderr)
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
