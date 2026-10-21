"""T15: `zoho-projects-axi mcp`, the canonical MCP entry point.

Two seams. The selection logic is tested through the CLI product seam `main(argv, ...)` with an injected `serve`
callable (a blocking stdio server cannot run in-process): `mcp` hands over to it and writes nothing to stdout,
anything else never does. The real thing is a subprocess smoke: the installed `zoho-projects-axi mcp` answers an MCP
initialize handshake and lists the six tools over stdio.
"""
import asyncio
import io
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from zoho_projects_axi.cli.main import main
from zoho_projects_axi.cli.registry import REGISTRY
from zoho_projects_axi.mcp_server.server import TOOLS

SIX = ["bug", "people", "project", "resolve", "task", "tasks"]


def run(argv, tmp_path, serve):
    env = {"HOME": str(tmp_path), "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env")}
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, env, out, err, serve=serve)
    return code, out.getvalue(), err.getvalue()


class Recorder:
    def __init__(self, raises=None):
        self.calls, self.raises = 0, raises

    def __call__(self):
        self.calls += 1
        if self.raises:
            raise self.raises


def test_mcp_hands_over_to_the_blocking_server_and_stdout_stays_untouched(tmp_path):
    serve = Recorder()
    assert run(["mcp"], tmp_path, serve) == (0, "", "")
    assert serve.calls == 1


@pytest.mark.parametrize("argv", [["mcp", "--help"], ["mcp", "-h"], ["--help"], ["home"], ["cache", "status"], []])
def test_nothing_but_a_plain_mcp_command_starts_the_server(argv, tmp_path):
    serve = Recorder()
    code, _, _ = run(argv, tmp_path, serve)
    assert code == 0 and serve.calls == 0


def test_mcp_help_documents_the_command(tmp_path):
    _, out, _ = run(["mcp", "--help"], tmp_path, Recorder())
    assert out.startswith("usage: zoho-projects-axi mcp\n") and "stdio" in out


def test_an_unknown_flag_is_a_usage_error_and_does_not_start_the_server(tmp_path):
    serve = Recorder()
    code, out, _ = run(["mcp", "--bogus"], tmp_path, serve)
    assert code == 2 and out.startswith("error: usage.unknown_flag") and serve.calls == 0


def test_a_crashing_server_exits_1_with_the_reason_on_stderr_only(tmp_path):
    code, out, err = run(["mcp"], tmp_path, Recorder(raises=RuntimeError("boom")))
    assert code == 1 and out == "" and "boom" in err and "Traceback" not in err


def test_ctrl_c_is_a_clean_stop(tmp_path):
    assert run(["mcp"], tmp_path, Recorder(raises=KeyboardInterrupt())) == (0, "", "")


def test_mcp_is_a_cli_command_but_not_one_of_the_six_tools():
    assert "mcp" in [s.name for s in REGISTRY]
    assert "mcp" not in TOOLS and sorted(TOOLS) == SIX


def test_importing_the_cli_does_not_load_the_mcp_sdk():
    script = "import sys, zoho_projects_axi.cli.main\nassert 'mcp' not in sys.modules, 'the MCP SDK must load lazily'\n"
    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_installed_zoho_projects_axi_mcp_answers_an_initialize_handshake_over_stdio(tmp_path):
    exe = Path(sys.executable).parent / "zoho-projects-axi"
    assert exe.exists(), "install the package with `pip install -e .`"
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env")}
    params = StdioServerParameters(command=str(exe), args=["mcp"], env=env, cwd=str(tmp_path))
    errlog = (tmp_path / "stderr.txt").open("w")

    async def go():
        async with Client(stdio_client(params, errlog=errlog)) as client:  # Client() performs `initialize`
            tools = await client.list_tools()
            return sorted(t.name for t in tools.tools)

    names = asyncio.run(go())
    errlog.close()
    assert names == SIX
    assert "deprecated" not in (tmp_path / "stderr.txt").read_text()  # the canonical entry carries no deprecation note
