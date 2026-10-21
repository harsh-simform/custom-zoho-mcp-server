"""CLI skeleton (T06). The seam is the product seam: argv/env in, (exit code, stdout, stderr) out.
`main(argv, env, stdout, stderr)` is exercised in-process; tests/test_cli_process.py runs the installed
`zoho-axi` entry point as a subprocess for what only a process can show.

Expected outputs are literals or hand-written goldens under tests/golden/cli, not recomputed from code.
"""
import io
import json
from pathlib import Path

import pytest

from zoho_axi.cli.main import main
from zoho_axi.cli.registry import REGISTRY, Arg, Flag, Spec
from zoho_axi.core.result import Result

GOLDEN = Path(__file__).parent / "golden" / "cli"
HELP_TOKEN_BUDGET = 250  # per-subcommand --help; tokens approximated as len(text) / 4 (no tokenizer dependency)

CREDS = {
    "ZOHO_AXI_CLIENT_ID": "id-SECRET-1",
    "ZOHO_AXI_CLIENT_SECRET": "secret-SECRET-2",
    "ZOHO_AXI_REFRESH_TOKEN": "refresh-SECRET-3",
}


def golden(name):
    return (GOLDEN / name).read_text().rstrip("\n")


def run(argv, tmp_path, extra_env=None, registry=REGISTRY):
    env = {"HOME": str(tmp_path), "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env")}
    env.update(extra_env or {})
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, env, out, err, registry)
    return code, out.getvalue().rstrip("\n"), err.getvalue()


# --- wired commands -----------------------------------------------------------------------------

def test_auth_status_offline_reports_credentials_and_never_prints_secrets(tmp_path):
    code, out, err = run(["auth", "status", "--offline"], tmp_path, CREDS)
    assert code == 0
    assert "credentials: complete" in out
    assert "token: unverified" in out
    assert "SECRET" not in out + err


def test_auth_status_without_credentials_points_at_login_help(tmp_path):
    code, out, _ = run(["auth", "status", "--offline"], tmp_path)
    assert code == 0
    assert 'credentials: "missing: client_id, client_secret, refresh_token"' in out
    assert "zoho-axi auth login-help" in out


def test_auth_login_help_keeps_placeholders_like_module_and_key(tmp_path):
    code, out, _ = run(["auth", "login-help"], tmp_path)
    assert code == 0
    assert "ZohoProjects.<module>.READ" in out
    assert "ZOHO_AXI_<KEY>" in out


def test_no_arguments_runs_home_and_exits_0(tmp_path):
    code, out, err = run([], tmp_path)
    assert (code, err) == (0, "")
    assert out == golden("home-unconfigured.txt")


def test_home_by_name_equals_bare_invocation(tmp_path):
    assert run(["home"], tmp_path) == run([], tmp_path)


def test_project_flag_overrides_the_configured_project(tmp_path):
    _, out, _ = run(["home", "--project", "999"], tmp_path, {"ZOHO_AXI_PROJECT_ID": "111"})
    assert 'project: "999"' in out  # TOON quotes digit-only strings


def test_json_flag_switches_the_encoding(tmp_path):
    code, out, _ = run(["home", "--json"], tmp_path)
    assert code == 0
    assert json.loads(out)["credentials"] == "missing: client_id, client_secret, refresh_token"


@pytest.mark.parametrize("argv", [["--json", "auth", "login-help"], ["auth", "--json", "login-help"],
                                  ["auth", "login-help", "--json"]])
def test_global_flags_are_accepted_before_between_and_after_the_command(argv, tmp_path):
    code, out, _ = run(argv, tmp_path)
    assert code == 0
    assert json.loads(out)["steps"]


def test_settings_warnings_appear_in_the_result_not_on_stderr(tmp_path):
    code, out, err = run(["home"], tmp_path, {"ZOHO_PORTAL_ID": "1"})  # legacy env source
    assert code == 0
    assert "deprecated config source in use (legacy-env)" in out
    assert err == ""


def test_config_problem_is_a_structured_error_with_exit_3(tmp_path):
    code, out, _ = run(["home"], tmp_path, {"ZOHO_AXI_DC": "mars"})
    assert code == 3
    assert out.splitlines()[0] == "error: config.invalid"


def test_debug_prints_the_request_count_to_stderr_only(tmp_path):
    code, out, err = run(["auth", "status", "--offline", "--debug"], tmp_path)
    assert code == 0
    assert err == "requests: 0\n"
    assert "requests" not in out


def test_without_debug_stderr_is_empty(tmp_path):
    assert run(["auth", "status", "--offline"], tmp_path)[2] == ""


# --- strict usage errors ------------------------------------------------------------------------

def test_unknown_flag_is_exit_2_with_a_closest_match_hint(tmp_path):
    code, out, _ = run(["auth", "status", "--offlin"], tmp_path)
    assert code == 2
    assert out.splitlines() == [
        "error: usage.unknown_flag",
        "message: unknown flag --offlin for `zoho-axi auth status`",
        "hint: Did you mean --offline? Run `zoho-axi auth status --help` for the flags.",
    ]


def test_flag_abbreviations_are_not_accepted(tmp_path):
    code, out, _ = run(["home", "--proj", "1"], tmp_path)
    assert code == 2
    assert "Did you mean --project?" in out


def test_unknown_flag_without_a_command_is_reported_as_a_flag(tmp_path):
    code, out, _ = run(["--projct", "x"], tmp_path)
    assert code == 2
    assert out.splitlines()[0] == "error: usage.unknown_flag"
    assert "Did you mean --project?" in out


def test_unknown_flag_error_honours_json(tmp_path):
    code, out, _ = run(["home", "--bogus", "--json"], tmp_path)
    assert code == 2
    assert json.loads(out)["error"] == "usage.unknown_flag"


def test_unknown_command_suggests_the_closest_one(tmp_path):
    code, out, _ = run(["hom"], tmp_path)
    assert code == 2
    assert out.splitlines()[0] == "error: usage.unknown_command"
    assert "Did you mean `zoho-axi home`?" in out


def test_unknown_subcommand_suggests_the_closest_one(tmp_path):
    code, out, _ = run(["auth", "statu"], tmp_path)
    assert code == 2
    assert "hint: Did you mean `zoho-axi auth status`?" in out


def test_group_without_subcommand_lists_the_subcommands(tmp_path):
    code, out, _ = run(["auth"], tmp_path)
    assert code == 2
    assert "status, login-help" in out


def test_usage_errors_never_touch_settings(tmp_path):
    # an invalid dc would be exit 3 if settings were loaded before the usage check
    code, _, _ = run(["home", "--bogus"], tmp_path, {"ZOHO_AXI_DC": "mars"})
    assert code == 2


# --- registry extension point: positionals, value flags, crashing commands ----------------------

def _echo(ctx):
    return Result("home", {"portal": "p", "project": f"{ctx.params['ref']}/{ctx.params['limit']}"})


def _crash(ctx):
    raise RuntimeError("boom")


FAKE = (
    Spec("thing", "echo", _echo, examples=("zoho-axi thing A", "zoho-axi thing A --limit 5"),
         args=(Arg("ref", "key"),), flags=(Flag("--limit", "max rows", metavar="N"),)),
    Spec("crash", "always fails", _crash, examples=("zoho-axi crash", "zoho-axi crash --debug")),
)


def test_positional_and_value_flag_reach_the_command(tmp_path):
    code, out, _ = run(["thing", "PROJ1-T1", "--limit", "5"], tmp_path, registry=FAKE)
    assert code == 0
    assert "project: PROJ1-T1/5" in out


def test_missing_positional_is_a_usage_error(tmp_path):
    code, out, _ = run(["thing", "--limit", "5"], tmp_path, registry=FAKE)
    assert code == 2
    assert "the following arguments are required: ref" in out


def test_a_value_flag_without_its_value_is_a_usage_error(tmp_path):
    code, _, _ = run(["thing", "A", "--limit"], tmp_path, registry=FAKE)
    assert code == 2


def test_an_unexpected_extra_argument_is_a_usage_error(tmp_path):
    code, _, _ = run(["thing", "A", "B", "--limit", "1"], tmp_path, registry=FAKE)
    assert code == 2


def test_unexpected_exception_is_internal_error_exit_1_without_a_traceback(tmp_path):
    code, out, err = run(["crash"], tmp_path, registry=FAKE)
    assert code == 1
    assert out.splitlines()[0] == "error: internal_error"
    assert "Traceback" not in out + err


def test_debug_adds_the_traceback_to_stderr_for_internal_errors(tmp_path):
    _, out, err = run(["crash", "--debug"], tmp_path, registry=FAKE)
    assert "Traceback" in err and "Traceback" not in out


# --- help ---------------------------------------------------------------------------------------

def test_top_level_help_lists_every_command_on_one_screen(tmp_path):
    code, out, _ = run(["--help"], tmp_path)
    assert code == 0
    assert out == golden("top-help.txt")
    assert len(out.splitlines()) <= 24
    for spec in REGISTRY:
        assert spec.name in out


def test_subcommand_help_golden(tmp_path):
    assert run(["auth", "status", "--help"], tmp_path) == (0, golden("auth-status-help.txt"), "")


@pytest.mark.parametrize("spec", REGISTRY + FAKE, ids=lambda s: s.name)
def test_every_subcommand_help_is_short_and_has_usage_and_two_examples(spec, tmp_path):
    code, out, _ = run([*spec.name.split(), "--help"], tmp_path, registry=REGISTRY + FAKE)
    assert code == 0
    assert out.startswith(f"usage: zoho-axi {spec.name}")
    assert len(out.splitlines()) <= 15
    assert len(out) / 4 <= HELP_TOKEN_BUDGET  # rough proxy: ~4 characters per token
    examples = out.split("examples:\n", 1)[1].splitlines()
    assert len(examples) == 2


def test_help_works_even_when_a_required_argument_is_missing(tmp_path):
    code, out, _ = run(["thing", "--help"], tmp_path, registry=FAKE)
    assert code == 0
    assert out.startswith("usage: zoho-axi thing <ref>")
