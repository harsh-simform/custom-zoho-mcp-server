"""The two Claude Code plugins (skill + CLI, MCP) and the marketplace that lists them stay consistent with the
package: manifests parse, every path they name exists, the shim is executable, and the skill the plugin ships is
byte-for-byte the one the package installs."""
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
SKILL, MCP = ROOT / "plugins" / "zoho-projects-axi-skill", ROOT / "plugins" / "zoho-projects-axi-mcp"
PLUGINS = (SKILL, MCP)


def test_the_marketplace_lists_both_plugins_at_existing_directories():
    assert MARKETPLACE["name"] == "zoho-projects-axi"
    entries = {p["name"]: p for p in MARKETPLACE["plugins"]}
    assert set(entries) == {"zoho-projects-axi-skill", "zoho-projects-axi-mcp"}
    for name, entry in entries.items():
        assert (ROOT / entry["source"]).is_dir(), name
        assert json.loads((ROOT / entry["source"] / ".claude-plugin" / "plugin.json").read_text())["name"] == name


@pytest.mark.parametrize("plugin", PLUGINS, ids=lambda p: p.name)
def test_each_plugin_has_a_runnable_shim_that_goes_through_uv(plugin):
    shim = plugin / "bin" / "zoho-projects-axi"
    assert os.access(shim, os.X_OK)
    text = shim.read_text()
    assert text.startswith("#!/bin/sh")
    assert 'uvx --quiet --from "$SPEC" zoho-projects-axi "$@"' in text
    assert "ZOHO_PROJECTS_AXI_SPEC" in text


def test_the_skill_plugin_ships_the_packaged_skill_and_a_session_start_hook():
    packaged = ROOT / "zoho_projects_axi" / "skills" / "zoho-projects-axi" / "SKILL.md"
    assert (SKILL / "skills" / "zoho-projects-axi" / "SKILL.md").read_text() == packaged.read_text()
    hooks = json.loads((SKILL / "hooks" / "hooks.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]
    assert hooks["type"] == "command"
    assert "bin/zoho-projects-axi session-start" in hooks["command"]
    assert hooks["command"].endswith("|| true")  # a missing uv must never show as a hook error


def test_the_mcp_plugin_runs_the_server_through_its_shim():
    server = json.loads((MCP / ".mcp.json").read_text())["mcpServers"]["zoho"]
    assert server == {"command": "${CLAUDE_PLUGIN_ROOT}/bin/zoho-projects-axi", "args": ["mcp"]}


def test_both_plugins_ship_the_same_login_command_and_it_only_allows_auth_calls():
    login = [(p / "commands" / "login.md").read_text() for p in PLUGINS]
    assert login[0] == login[1]
    allowed = re.search(r"^allowed-tools: (.*)$", login[0], re.M).group(1)
    assert allowed == "Bash(zoho-projects-axi auth login:*), Bash(zoho-projects-axi auth status:*)"
    assert "zoho-projects-axi auth login" in login[0]
