"""T15: the packaging metadata and the docs cannot drift from the code.

README.md and CHANGELOG.md are checked against what exists: every registered CLI command is documented, every
`zoho-axi ...` line in a README code block parses against the real registry (the "link check" of the README's
commands), the measured numbers it quotes are re-measured here, and the removed MCP tools are not mentioned.
"""
import importlib
import json
import re
import shlex
from pathlib import Path

import pytest
from test_mcp_server import list_tools

from zoho_axi.cli.parser import parse
from zoho_axi.cli.registry import GLOBAL_FLAGS, REGISTRY
from zoho_axi.core.config import VALID_DATA_CENTERS, _SPEC
from zoho_axi.core.errors import ZohoAxiError
from zoho_axi.mcp_server.server import TOOLS

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
CHANGELOG = (ROOT / "CHANGELOG.md").read_text()
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())

# The 15 tools of 0.1.x. They exist only in the CHANGELOG now.
REMOVED_TOOLS = (
    "configure_oauth", "list_portals", "list_projects", "select_portal_and_project", "switch_portal",
    "list_selected_portals", "remove_portal_selection", "reset_selection", "get_project_context",
    "get_tasklist_context", "get_milestone_context", "get_task_context", "get_bug_context", "list_users", "list_tags",
)


# --- pyproject ---------------------------------------------------------------------------------------------

def test_the_version_is_0_2_0_and_the_changelog_leads_with_it():
    assert PYPROJECT["project"]["version"] == "0.2.0"
    first = re.search(r"^## \[?(\d+\.\d+\.\d+)\]?", CHANGELOG, re.M)
    assert first and first.group(1) == PYPROJECT["project"]["version"]


def test_python_floor_and_the_tomli_fallback_for_python_3_10():
    project = PYPROJECT["project"]
    assert project["requires-python"] == ">=3.10"
    assert any(d.startswith("tomli") and "python_version < '3.11'" in d for d in project["dependencies"])
    assert any(d.startswith("mcp") for d in project["dependencies"])
    assert any(d.startswith("pytest") for d in project["optional-dependencies"]["dev"])


@pytest.mark.parametrize("script", ["zoho-axi", "zoho", "zoho-projects-mcp"])
def test_every_console_script_points_at_a_real_callable(script):
    module, _, attr = PYPROJECT["project"]["scripts"][script].partition(":")
    assert callable(getattr(importlib.import_module(module), attr))


def test_the_canonical_entry_is_zoho_axi_and_the_legacy_scripts_are_marked_deprecated():
    text = (ROOT / "pyproject.toml").read_text()
    assert PYPROJECT["project"]["scripts"]["zoho-axi"] == "zoho_axi.cli.main:console_main"
    assert "Deprecated" in text.split("\nzoho-projects-mcp")[0].splitlines()[-1]


def test_the_skill_is_packaged():
    assert PYPROJECT["tool"]["setuptools"]["package-data"]["zoho_axi"] == ["skills/*/SKILL.md"]
    assert (ROOT / "zoho_axi" / "skills" / "zoho-axi" / "SKILL.md").is_file()


def test_the_python_dash_m_shim_is_still_marked_deprecated():
    assert "deprecated" in (ROOT / "zoho_mcp" / "__main__.py").read_text()


# --- README: nothing stale ---------------------------------------------------------------------------------

def test_the_readme_mentions_no_removed_tool_and_no_plan_section_refs():
    for name in REMOVED_TOOLS:
        assert name not in README, name
    assert not re.search(r"plan\s*§|§\s*\d", README)
    assert ".env.example" not in README and "select_portal" not in README


def test_the_changelog_names_every_removed_tool():
    for name in REMOVED_TOOLS:
        assert name in CHANGELOG, name


# --- README: derived from the code -------------------------------------------------------------------------

@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.name)
def test_every_registered_command_is_in_the_readme_quick_reference(spec):
    assert f"zoho-axi {spec.name}" in README


def test_every_global_flag_and_data_center_is_in_the_readme():
    for flag in GLOBAL_FLAGS:
        assert flag.name in README, flag.name
    for dc in VALID_DATA_CENTERS:
        assert f"`{dc}`" in README, dc


def test_every_setting_is_named_in_the_readme_or_the_example_it_points_at():
    assert "config.example.toml" in README and (ROOT / "config.example.toml").is_file()
    for key in _SPEC:
        assert key in README, key


def readme_commands():
    """Every `zoho-axi ...` command the README shows: lines of fenced blocks and inline `code` spans. Lines with a
    pipe or a `<placeholder>` are templates, not runnable examples."""
    candidates = []
    for block in re.findall(r"```(?:bash|sh|console)?\n(.*?)```", README, re.S):
        candidates += [line.strip().removeprefix("$ ") for line in block.splitlines()]
    candidates += re.findall(r"`(zoho-axi[^`]*)`", README)
    for line in candidates:
        if line.startswith("zoho-axi") and "|" not in line and "<" not in line:
            yield line


def test_the_readme_has_a_real_set_of_runnable_command_examples():
    assert len(list(readme_commands())) >= 30


@pytest.mark.parametrize("line", sorted(set(readme_commands())))
def test_every_zoho_axi_command_line_in_the_readme_parses_against_the_registry(line):
    argv = shlex.split(line)[1:]
    try:
        inv = parse(argv, REGISTRY)
    except ZohoAxiError as exc:  # a UsageError names what is wrong
        pytest.fail(f"README line {line!r} is rejected: {exc.code} {exc.message}")
    assert inv.help or inv.spec is not None


def test_the_quoted_tool_schema_numbers_are_the_measured_ones():
    wire = json.dumps([t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in list_tools(Path("."))],
                      separators=(",", ":"))
    assert len(TOOLS) == 6 and f"{len(wire):,}" in README and f"{-(-len(wire) // 4)}" in README
    assert "1,421" in README and "5,686" in README  # the 15 legacy tools, as recorded in tests/test_mcp_server.py
    assert "len(text) / 4" in README or "chars / 4" in README  # the estimator is named


def test_the_readme_sources_every_budget_it_quotes():
    for source in ("tests/test_mcp_server.py", "tests/test_cmd_task.py", "tests/test_cmd_bug.py",
                   "tests/test_cmd_fixtures.py", "tests/test_session_start.py", "tests/README.md"):
        assert source in README, source
        assert (ROOT / source).is_file(), source


def test_the_readme_documents_the_operational_topics():
    for needle in ("uv tool install git+", "pipx install git+", "claude mcp add --scope user",
                   "claude_desktop_config.json", "hooks install --yes", "skill install --yes",
                   "cache clear --yes", "ZOHO_AXI_LIVE=1", "scripts/record_fixture.py", "chmod 600",
                   "project use", "rate_limited", "login-help"):
        assert needle in README, needle


def test_the_readme_is_honest_about_what_is_unverified():
    known = README.split("## Known limitations", 1)[1]
    for needle in ("synthetic", "/issues", "/phases", "associated_task", "page_info", "429", "ZohoSearch.securesearch.READ",
                   "claude mcp list", "record_fixture.py"):
        assert needle in known, needle
