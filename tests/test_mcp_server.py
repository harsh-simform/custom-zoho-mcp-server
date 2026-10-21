"""The MCP front-end (T13), tested at its one seam: an in-process MCP client calls a tool and reads the text back.
Zoho is replayed below it exactly as in the CLI tests (a ReplayTransport-backed gateway injected into the server,
the same injection style as tests/support/world.py). Parity tests run the CLI seam on one World and the MCP seam on
another (separate HOME, so separate caches) over the same fixtures and compare the texts. Expected values are
literals or come from the CLI seam, never from the server code.
"""
import asyncio
import base64
import json
import logging
import re
import shlex
import subprocess
import sys

import pytest
from mcp import Client
from support.fixtures import BUG, PORTAL, PROJECT, TASK, replay_for
from support.replay import ReplayTransport
from support.tokens import assert_token_budget
from support.world import TODAY, World
from test_attachments_images import PNG, rec, task_replay

from zoho_axi.cli.registry import REGISTRY
from zoho_axi.core.config import load_settings
from zoho_axi.core.errors import ConfigError
from zoho_axi.core.gateway import ZohoGateway
from zoho_axi.mcp_server.server import RedactSecrets, build_server

SIX = ["bug", "people", "project", "resolve", "task", "tasks"]
TOOL_SCHEMA_BUDGET = 800  # tokens (proxy: chars / 4), all six tools together, as the T13 ticket asks


def fixture_world(tmp_path, name):
    return World(tmp_path / name, configured=False, ZOHO_AXI_PORTAL_ID=PORTAL, ZOHO_AXI_PROJECT_ID=PROJECT)


def server_for(world, replay):
    settings = load_settings(env=world.env, legacy_env_file=world.tmp / "none.env")
    gateway = ZohoGateway(settings, transport=replay, token_provider=lambda: "tok-abc", sleep=lambda s: None)
    return build_server(settings, gateway=gateway, env=world.env, today=TODAY)


def with_client(server, action):
    async def go():
        async with Client(server) as client:
            return await action(client)

    return asyncio.run(go())


def call(world, replay, tool, **args):
    """The MCP seam: the CallToolResult of one in-process tool call."""
    return with_client(server_for(world, replay), lambda c: c.call_tool(tool, args))


def text_of(result):
    assert result.content[0].type == "text"
    return result.content[0].text


def list_tools(tmp_path):
    return with_client(server_for(World(tmp_path), ReplayTransport()), lambda c: c.list_tools()).tools


# --- the tool surface ------------------------------------------------------------------------------------------

def test_the_server_exposes_exactly_six_tools(tmp_path):
    assert sorted(t.name for t in list_tools(tmp_path)) == SIX


def test_tool_arguments_mirror_the_cli_flags(tmp_path):
    props = {t.name: set(t.input_schema["properties"]) for t in list_tools(tmp_path)}
    common = {"project", "fresh", "full", "format"}  # --json is `format`; --debug stays CLI-only
    assert props["task"] == common | {"ref", "comments", "history", "subtasks", "attachments", "images"}
    assert props["bug"] == common | {"ref", "comments", "attachments", "images", "task"}
    assert props["tasks"] == common | {"tasklist", "milestone", "status", "owner", "limit", "page"}
    assert props["people"] == common | {"users", "tags", "limit"}
    assert props["project"] == common | {"lists"}
    assert props["resolve"] == common | {"text", "check"}


def test_required_arguments_and_types(tmp_path):
    schemas = {t.name: t.input_schema for t in list_tools(tmp_path)}
    assert schemas["task"]["required"] == ["ref"] and schemas["bug"]["required"] == ["ref"]
    assert schemas["resolve"]["required"] == ["text"]
    assert "required" not in schemas["tasks"]
    assert schemas["tasks"]["properties"]["limit"] == {"type": "integer"}
    assert schemas["task"]["properties"]["comments"] == {"type": "boolean"}
    assert schemas["task"]["properties"]["format"]["enum"] == ["toon", "json"]


def test_total_tool_schema_size_is_recorded_and_within_the_budget(tmp_path):
    """The number the ticket asks to record: every tool as list_tools emits it, compact JSON, chars / 4.
    Measured when written: 2601 chars = 650 tokens for six tools, against 5686 chars = 1421 tokens for the 15
    legacy tools (SDK-default schemas for the new six, titles and null branches included, were 1125)."""
    wire = json.dumps([t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in list_tools(tmp_path)],
                      separators=(",", ":"))
    measured = assert_token_budget(wire, TOOL_SCHEMA_BUDGET, label="mcp tool schemas (6 tools)")
    assert measured <= 700  # a ceiling closer to the measured value, so growth is noticed in review


def test_tool_descriptions_are_one_or_two_short_lines(tmp_path):
    for tool in list_tools(tmp_path):
        assert "\n" not in tool.description and len(tool.description) <= 160, tool.name


# --- calls -----------------------------------------------------------------------------------------------------

def test_task_call_returns_the_toon_text_and_mcp_call_form_hints(tmp_path):
    replay = replay_for("tasks/detail", "task_comments/v3_ok", "task_attachments/image_and_non_image")
    result = call(fixture_world(tmp_path, "m"), replay, "task", ref=TASK)
    assert not result.is_error and len(result.content) == 1
    text = text_of(result)
    assert text.startswith("task: DM1-T2\n")
    assert "help[" in text and "task ref=DM1-T2 comments=true" in text  # a tool call, not a `zoho-axi` command
    assert "zoho-axi" not in text


def test_format_json_returns_json(tmp_path):
    replay = replay_for("projects/detail", "tasks/list")
    result = call(fixture_world(tmp_path, "m"), replay, "tasks", format="json", limit=2)
    doc = json.loads(text_of(result))
    assert [t["key"] for t in doc["tasks"]] == ["DM1-T1", "DM1-T2"]


def test_the_server_starts_from_config_file_credentials_alone_with_no_prior_configure_call(tmp_path):
    cfg = tmp_path / ".config" / "zoho-axi" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'client_id="file-id"\nclient_secret="file-secret"\nrefresh_token="file-refresh"\n'
                   f'portal_id="{PORTAL}"\nproject_id="{PROJECT}"\n')
    cfg.chmod(0o600)
    env = {"HOME": str(tmp_path), "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env")}
    settings = load_settings(env=env, legacy_env_file=tmp_path / "none.env")
    replay = replay_for("projects/detail", "tasklists/list", "milestones/v3_404_legacy_ok", "users/list", "tags/list")
    replay.add("POST", "https://accounts.zoho.com/oauth/v2/token", json={"access_token": "t-1", "expires_in": 3600})
    gateway = ZohoGateway(settings, transport=replay, sleep=lambda s: None)  # no injected token: the real OAuth path
    server = build_server(settings, gateway=gateway, env=env, today=TODAY)
    result = with_client(server, lambda c: c.call_tool("project", {}))
    assert not result.is_error and text_of(result).startswith(f'project: "{PROJECT}"')
    token_post = next(c for c in replay.calls if c.method == "POST")
    assert token_post.data["refresh_token"] == "file-refresh"  # the config file's credential was used


# --- parity with the CLI ---------------------------------------------------------------------------------------

PROJECT_SET = ("projects/detail", "tasklists/list", "milestones/v3_404_legacy_ok", "users/list", "tags/list")
TASK_SET = ("tasks/detail", "task_comments/v3_ok", "task_attachments/image_and_non_image")
BUG_SET = ("bugs/detail_with_task", "bug_comments/v3_ok", "bug_attachments/v3_nested_file_info")
RESOLVE_TEXT = "why does DM1-T1 loop? see the comments, and bug 45 as well"

# (id, fixture scenarios, CLI argv, MCP tool, MCP arguments): a default and at least one slice per command
CASES = [
    ("project", PROJECT_SET, ["project"], "project", {}),
    ("project-lists", PROJECT_SET, ["project", "--lists"], "project", {"lists": True}),
    ("tasks", ("tasks/list",), ["tasks"], "tasks", {}),
    ("tasks-owner", ("tasks/list",), ["tasks", "--owner", "sana", "--limit", "3"], "tasks", {"owner": "sana", "limit": 3}),
    ("task", TASK_SET, ["task", TASK], "task", {"ref": TASK}),
    ("task-comments", TASK_SET, ["task", TASK, "--comments"], "task", {"ref": TASK, "comments": True}),
    ("task-attachments-full", TASK_SET, ["task", TASK, "--attachments", "--full"], "task",
     {"ref": TASK, "attachments": True, "full": True}),
    ("bug", BUG_SET, ["bug", BUG], "bug", {"ref": BUG}),
    ("bug-comments", BUG_SET, ["bug", BUG, "--comments"], "bug", {"ref": BUG, "comments": True}),
    ("people", ("users/list", "tags/list"), ["people"], "people", {}),
    ("people-users", ("users/list",), ["people", "--users"], "people", {"users": True}),
    ("resolve", (), ["resolve", RESOLVE_TEXT], "resolve", {"text": RESOLVE_TEXT}),
]


def as_call_form(cli_text):
    """A CLI-form expand hint inside data text (`--full`, `--limit 24`) spelled as the MCP form (`full=true`, `limit=24`)."""
    return re.sub(r"--([a-z][a-z-]*)(?: ([^\s\-)][^\s)]*))?", lambda m: f"{m[1].replace('-', '_')}={m[2] or 'true'}", cli_text)


def hint_call(line):
    """(command, {param: value}) of one help line, in either form: `zoho-axi task KEY --comments --full` or
    `task ref=KEY comments=true full=true`."""
    words = shlex.split(line)
    if words[0] == "zoho-axi":
        command, rest, params, i = words[1], words[2:], {}, 0
        while i < len(rest):
            if rest[i].startswith("--"):
                name = rest[i][2:].replace("-", "_")
                if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                    params[name], i = rest[i + 1], i + 2
                else:
                    params[name], i = "true", i + 1
            else:
                params["ref"], i = rest[i], i + 1  # the only positional the hinted commands have
        return command, params
    return words[0], dict(w.split("=", 1) for w in words[1:])


def cli_and_mcp(tmp_path, scenarios, argv, tool, args):
    cli_world, mcp_world = fixture_world(tmp_path, "cli"), fixture_world(tmp_path, "mcp")
    _, cli_json, _ = cli_world.run([*argv, "--json"], replay_for(*scenarios))
    _, cli_toon, _ = cli_world.run(argv, replay_for(*scenarios), env={"HOME": str(tmp_path / "cli2")})
    mcp_json = text_of(call(mcp_world, replay_for(*scenarios), tool, **args, format="json"))
    mcp_toon = text_of(call(fixture_world(tmp_path, "mcp2"), replay_for(*scenarios), tool, **args))
    return cli_json, cli_toon, mcp_json, mcp_toon


@pytest.mark.parametrize("case_id,scenarios,argv,tool,args", CASES, ids=[c[0] for c in CASES])
def test_a_tool_call_returns_what_the_cli_prints(tmp_path, case_id, scenarios, argv, tool, args):
    cli_json, cli_toon, mcp_json, mcp_toon = cli_and_mcp(tmp_path, scenarios, argv, tool, args)

    cli_doc, mcp_doc = json.loads(cli_json), json.loads(mcp_json)
    assert "error" not in cli_doc and "error" not in mcp_doc
    cli_help, mcp_help = cli_doc.pop("help", []), mcp_doc.pop("help", [])
    # data and warnings are identical; the only other call-form difference is the spelling of expand hints
    assert json.loads(as_call_form(json.dumps(cli_doc))) == mcp_doc
    # help differs only in form: the same calls with the same parameters
    assert [hint_call(h) for h in cli_help] == [hint_call(h) for h in mcp_help]
    assert all(h.startswith("zoho-axi ") for h in cli_help) and not any("zoho-axi" in h for h in mcp_help)

    # the TOON text (what the tool really returns) is the same up to its help block
    cli_data, _, cli_tail = cli_toon.partition("\nhelp[")
    mcp_data, _, mcp_tail = mcp_toon.partition("\nhelp[")
    assert as_call_form(cli_data) == mcp_data
    assert bool(cli_tail) == bool(mcp_tail) == bool(cli_help)


def test_parity_covers_a_default_and_a_slice_for_every_tool():
    assert {tool for _, _, _, tool, _ in CASES} == set(SIX)
    assert sum(1 for c in CASES if c[3] == "task") >= 2 and sum(1 for c in CASES if c[3] == "bug") >= 2


# --- errors ----------------------------------------------------------------------------------------------------

def test_a_failure_is_a_tool_error_carrying_the_same_structured_text_as_the_cli(tmp_path):
    cli_world, mcp_world = World(tmp_path / "cli", configured=False), World(tmp_path / "mcp", configured=False)
    code, cli_out, _ = cli_world.run(["tasks"])
    result = call(mcp_world, ReplayTransport(), "tasks")
    assert code == 3 and result.is_error
    assert text_of(result).replace(str(mcp_world.tmp), "<home>") == cli_out.replace(str(cli_world.tmp), "<home>")
    assert text_of(result).startswith("error: config.project_missing\nmessage: No portal/project to use.")


def test_a_usage_error_keeps_its_code(tmp_path):
    result = call(fixture_world(tmp_path, "m"), replay_for("tasks/list"), "tasks", limit=0)
    assert result.is_error and text_of(result).startswith("error: usage.invalid_arguments\n")


def test_json_format_errors_are_json_too(tmp_path):
    result = call(World(tmp_path, configured=False), ReplayTransport(), "tasks", format="json")
    assert result.is_error and json.loads(text_of(result))["error"] == "config.project_missing"


def test_missing_credentials_are_an_auth_error_on_the_call_not_a_startup_failure(tmp_path):
    env = {"HOME": str(tmp_path), "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env"),
           "ZOHO_AXI_PORTAL_ID": PORTAL, "ZOHO_AXI_PROJECT_ID": PROJECT}
    settings = load_settings(env=env, legacy_env_file=tmp_path / "none.env")
    replay = ReplayTransport()
    server = build_server(settings, gateway=ZohoGateway(settings, transport=replay), env=env)
    result = with_client(server, lambda c: c.call_tool("people", {}))
    assert result.is_error and text_of(result).startswith("error: auth.missing\n")
    assert replay.calls == []


def broken_config_world(tmp_path):
    world = fixture_world(tmp_path, "cfg")
    world.config_path.parent.mkdir(parents=True)
    world.config_path.write_text("this is = = not toml\n")
    return world


def test_unloadable_settings_answer_every_call_with_the_config_error(tmp_path):
    world = broken_config_world(tmp_path)
    problem = ConfigError("config.invalid", "config.toml is not valid TOML", hint="Fix the syntax.")
    server = build_server(None, startup_error=problem, env=world.env)
    result = with_client(server, lambda c: c.call_tool("resolve", {"text": "DM1-T1"}))
    assert result.is_error and text_of(result).startswith("error: config.")
    assert "message: config.toml is not valid TOML" not in text_of(result)  # the live error replaces the stale one


def test_a_startup_config_error_clears_once_the_file_is_fixed_without_a_restart(tmp_path):
    world = fixture_world(tmp_path, "cfg")
    replay = replay_for("tasks/detail", "task_comments/v3_ok", "task_attachments/image_and_non_image")
    settings = load_settings(env=world.env, legacy_env_file=world.tmp / "none.env")
    gateway = ZohoGateway(settings, transport=replay, token_provider=lambda: "tok-abc", sleep=lambda s: None)
    world.config_path.parent.mkdir(parents=True)
    world.config_path.write_text("this is = = not toml\n")
    startup_error = ConfigError("config.invalid", "config.toml is not valid TOML", hint="Fix the syntax.")
    server = build_server(None, gateway=gateway, env=world.env, today=TODAY, startup_error=startup_error)

    async def scenario(client):
        first = await client.call_tool("task", {"ref": TASK})
        world.config_path.write_text("")  # the user fixes the file; env still supplies credentials and project
        second = await client.call_tool("task", {"ref": TASK})
        return first, second

    first, second = with_client(server, scenario)
    assert first.is_error and text_of(first).startswith("error: config.")
    assert not second.is_error and text_of(second).startswith("task: DM1-T2\n")


# --- images ----------------------------------------------------------------------------------------------------

def image_world(tmp_path):
    return fixture_world(tmp_path, "img")


def test_images_are_returned_inline_only_when_asked_and_nothing_is_written_to_disk(tmp_path):
    replay = task_replay(rec("shot.png"), rec("notes.pdf", kind="application/pdf"))
    world = image_world(tmp_path)
    result = call(world, replay, "task", ref=TASK, images=True)
    assert not result.is_error and [c.type for c in result.content] == ["text", "image"]
    assert result.content[1].mime_type == "image/png" and base64.b64decode(result.content[1].data) == PNG
    assert "shot.png" in text_of(result) and "images[1]" in text_of(result)
    assert not (world.tmp / ".cache" / "zoho-axi" / "attachments").exists()  # the CLI saves files there; MCP does not


def test_the_default_call_downloads_no_image(tmp_path):
    replay = task_replay(rec("shot.png"))
    result = call(image_world(tmp_path), replay, "task", ref=TASK)
    assert [c.type for c in result.content] == ["text"]
    assert not [c for c in replay.calls if c.url.startswith("https://download.")]


def test_images_over_the_count_cap_are_warned_about_not_failed(tmp_path):
    replay = task_replay(*[rec(f"{n}.png") for n in "abcd"])
    world = fixture_world(tmp_path, "img")
    world.env["ZOHO_AXI_MAX_IMAGES"] = "2"
    result = call(world, replay, "task", ref=TASK, images=True)
    assert [c.type for c in result.content] == ["text", "image", "image"]
    assert "2 more images not downloaded (max_images is 2): c.png, d.png" in text_of(result)


@pytest.mark.parametrize("served, mime", [
    ("image/png", "image/png"), ("image/jpeg", "image/jpeg"), ("image/jpg", "image/jpeg"),
    ("image/gif", "image/gif"), ("image/webp", "image/webp"), ("IMAGE/PNG; charset=binary", "image/png"),
])
def test_inline_images_are_sent_with_a_whitelisted_mime_type(tmp_path, served, mime):
    replay = task_replay(rec("pic.bin", kind=served))
    result = call(image_world(tmp_path), replay, "task", ref=TASK, images=True)
    assert [c.type for c in result.content] == ["text", "image"]
    assert result.content[1].mime_type == mime


@pytest.mark.parametrize("kind", ["image/svg+xml", "image/tiff", "image/x-icon", "image/bmp"])
def test_other_image_types_are_skipped_with_a_warning_line_not_sent(tmp_path, kind):
    replay = task_replay(rec("shot.png"), rec("odd.xyz", kind=kind))
    result = call(image_world(tmp_path), replay, "task", ref=TASK, images=True)
    assert not result.is_error and [c.type for c in result.content] == ["text", "image"]
    assert result.content[1].mime_type == "image/png"
    assert f"odd.xyz not shown inline ({kind} is not png, jpeg, gif or webp)" in text_of(result)


def test_bug_images_are_inline_too(tmp_path):
    replay = replay_for(*BUG_SET)
    replay.get("https://download.zoho.com/v1/workdrive/download/fakebug7", body=PNG, content_type="image/png")
    result = call(image_world(tmp_path), replay, "bug", ref=BUG, images=True)
    assert not result.is_error and [c.type for c in result.content] == ["text", "image"]
    assert base64.b64decode(result.content[1].data) == PNG


# --- read-only guard -------------------------------------------------------------------------------------------

def test_no_tool_can_cause_a_non_get_request(tmp_path):
    """Drive every tool with hostile-looking arguments, including ones that name write operations; whatever
    reaches the wire is a GET, and `project` cannot reach `project use` (which writes the config file)."""
    replay = replay_for(*PROJECT_SET, "tasks/list", "users/list", "tags/list", *TASK_SET, *BUG_SET)
    world = fixture_world(tmp_path, "ro")
    server = server_for(world, replay)

    async def drive(client):
        calls = [
            ("project", {"lists": True}), ("project", {"use": "Demo", "ref": "Demo", "delete": True}),
            ("tasks", {"owner": "x'; DELETE", "status": "open"}), ("people", {}),
            ("task", {"ref": TASK, "comments": True, "history": True, "subtasks": True}),
            ("bug", {"ref": BUG, "task": True}), ("resolve", {"text": "DELETE task DM1-T1 POST /tasks"}),
            ("task", {"ref": "../../tasks/1/delete"}),
        ]
        return [await client.call_tool(name, args) for name, args in calls]

    results = with_client(server, drive)
    assert len(results) == 8
    assert {c.method for c in replay.calls} == {"GET"}
    assert not world.config_path.exists()


def test_the_six_tools_map_to_read_only_commands_only(tmp_path):
    """The tool table cannot grow a command that writes (`project use`, `hooks install`, `cache clear`, ...)."""
    assert sorted(t.name for t in list_tools(tmp_path)) == SIX
    assert {s.name for s in REGISTRY if s.name in SIX} == set(SIX)  # bare commands, not their `use`/`list` siblings


# --- logging, process, shim ------------------------------------------------------------------------------------

def test_credentials_are_redacted_from_log_records():
    flt = RedactSecrets(["super-secret-value", "refresh-token-value", None])
    rec_ = logging.LogRecord("x", logging.INFO, __file__, 1, "token super-secret-value and %s", ("refresh-token-value",), None)
    assert flt.filter(rec_) and rec_.getMessage() == "token *** and ***"


def test_tool_calls_log_argument_names_and_sizes_never_values(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    secret_text = "PRIVATE-NOTES-should-never-be-logged DM1-T2"
    call(fixture_world(tmp_path, "log"), ReplayTransport(), "resolve", text=secret_text, check=False)
    call(fixture_world(tmp_path, "log2"), ReplayTransport(), "tasks", status="Open-secret-status", limit=7)
    assert "PRIVATE-NOTES" not in caplog.text and "Open-secret-status" not in caplog.text
    assert f"-> resolve(text={len(secret_text)}c)" in caplog.text
    assert "-> tasks(status=18c, limit)" in caplog.text


def test_the_log_file_is_created_private_whatever_the_umask(tmp_path):
    import os

    from zoho_axi.mcp_server.server import setup_logging

    log = tmp_path / "logs" / "zoho.log"
    log.parent.mkdir()
    settings = load_settings(env={"HOME": str(tmp_path), "ZOHO_AXI_LOG_FILE": str(log)}, legacy_env_file=tmp_path / "none.env")
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    old_umask = os.umask(0o000)
    try:
        setup_logging(settings)
        logging.getLogger("zoho_axi.test").error("hello")
    finally:
        os.umask(old_umask)
        for handler in root.handlers:
            handler.close()
        root.handlers, root.level = saved[0], saved[1]
    assert log.stat().st_mode & 0o777 == 0o600
    assert "hello" in log.read_text()


def test_importing_the_server_has_no_side_effects(tmp_path):
    script = (
        "import logging, sys\n"
        "root = (list(logging.getLogger().handlers), logging.getLogger().level)\n"
        "import zoho_axi.mcp_server.server\n"
        "assert (list(logging.getLogger().handlers), logging.getLogger().level) == root, 'logging configured at import'\n"
        "assert 'requests' not in sys.modules\n"
        "print('ok')\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", script], env={"HOME": str(tmp_path), "PATH": ""},
                          capture_output=True, text=True, cwd=tmp_path)
    assert done.stdout.strip() == "ok", done.stderr
    assert list(tmp_path.iterdir()) == []
