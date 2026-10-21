"""Seam: render(result, mode, full) / render_error(exc) -> Rendered(text, exit_code).

Expected output lives in tests/golden/render/*.txt, hand-written from the illustrative output in
docs/specs/00-overview.md (and the T05 ticket), never produced by running the renderer. Inline
expectations are literals or plain arithmetic (e.g. 3300 chars with a 1500 limit omits 1800).
Field names in the Results below are the already-normalized names the T02 gateway will emit; the
shapes for bugs/history/etc. are invented-but-plausible and marked as such where used.
"""
import json
from pathlib import Path

import pytest

from zoho_axi.core.errors import AuthError, ConfigError, UpstreamError, UsageError, ZohoAxiError
from zoho_axi.core.render import render, render_error
from zoho_axi.core.result import Hint, Result, Truncation

GOLDEN = Path(__file__).parent / "golden" / "render"


def golden(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8").rstrip("\n")


def task_detail_result(**overrides) -> Result:
    kwargs = dict(
        entity="task",
        payload={
            "key": "PROJ1-T1000",
            "title": "Fix login redirect loop",
            "status": "In Progress",
            "owner": "Asha K",
            "priority": "High",
            "due": "2026-10-30",
            "tasklist": "Auth",
            "description": "Users bounce between /login and /home after the OAuth callback.",
            "comments": [
                {"by": "Asha K", "at": "2026-10-18", "text": "Repro on staging only"},
                {"by": "Ravi P", "at": "2026-10-19", "text": "Cleared the cookie jar and it still loops"},
            ],
        },
        counts={"comments": 7, "subtasks": 2, "attachments": 3, "history": 4},
        help=[
            Hint("task", {"ref": "PROJ1-T1000", "comments": True, "full": True}, positional=("ref",)),
            Hint("task", {"ref": "PROJ1-T1000", "history": True}, positional=("ref",)),
            Hint("task", {"ref": "PROJ1-T1000", "images": True}, positional=("ref",)),
        ],
    )
    kwargs.update(overrides)
    return Result(**kwargs)


def test_detail_renders_header_fields_counts_text_sections_and_help():
    out = render(task_detail_result(), mode="cli", full=False)
    assert out.text == golden("detail.txt")
    assert out.exit_code == 0


def test_list_shows_aggregates_above_rows_with_only_the_minimal_columns():
    result = Result(
        entity="task",
        payload=[
            # extra keys (priority, due) must not appear: list rows carry 4 fields
            {"key": "PROJ1-T1", "title": "Fix login redirect loop", "status": "In Progress",
             "owner": "Asha K", "priority": "High", "due": "2026-10-30"},
            {"key": "PROJ1-T2", "title": "Add SSO", "status": "Open", "owner": "Ravi P", "priority": "Low"},
            {"key": "PROJ1-T3", "title": "Upgrade deps", "status": "Open", "priority": "Low"},
        ],
        counts={"total": 57, "open": 40, "closed": 17},
        help=[Hint("task", {"ref": "PROJ1-T1"}, positional=("ref",))],
    )
    assert render(result, mode="cli", full=False).text == golden("list.txt")


def test_empty_list_is_an_explicit_zero_results_line_never_empty_output():
    result = Result(
        entity="task",
        payload=[],
        help=[Hint("tasks", {"status": "all"})],
    )
    out = render(result, mode="cli", full=False)
    assert out.text == golden("empty.txt")
    assert out.exit_code == 0


def minimal_task(description: str, comment_text: str = "ok") -> Result:
    return Result(
        entity="task",
        payload={
            "key": "PROJ1-T1000",
            "title": "Fix login redirect loop",
            "status": "In Progress",
            "description": description,
            "comments": [{"by": "Asha K", "at": "2026-10-18", "text": comment_text}],
        },
        help=[Hint("task", {"ref": "PROJ1-T1000", "full": True}, positional=("ref",))],
    )


def test_over_limit_text_is_cut_with_size_hint_and_expand_flag_golden():
    result = minimal_task("word " * 660, comment_text="ab" * 360)  # 3300 and 720 chars
    assert render(result, mode="cli", full=False).text == golden("truncated.txt")


def test_detail_text_limit_is_1500_chars_and_mcp_names_the_param_not_the_flag():
    kept = ("word " * 300).rstrip()
    out = render(minimal_task("word " * 660), mode="mcp", full=False).text
    assert f'description: "{kept} ... (+1.8k chars, full=true)"' in out


def test_text_at_the_limit_is_untouched_and_one_char_over_is_cut_with_exact_count():
    at_limit = "a" * 1500
    assert f"description: {at_limit}\n" in render(minimal_task(at_limit), full=False).text + "\n"
    over = render(minimal_task("a" * 1501), full=False).text
    assert f'description: "{"a" * 1500} ... (+1 chars, --full)"' in over


def test_row_text_limit_is_300_chars():
    out = render(minimal_task("short", comment_text="b" * 301), full=False).text
    assert f'"{"b" * 300} ... (+1 chars, --full)"' in out
    assert "b" * 301 not in out


@pytest.mark.parametrize("omitted, shown", [
    (1, "1"), (420, "420"), (999, "999"), (1000, "1.0k"), (1800, "1.8k"), (1999, "1.9k"),
    (9999, "9.9k"), (10000, "10k"), (12345, "12k"),
])
def test_size_hint_uses_k_units_above_999(omitted, shown):
    out = render(minimal_task("x" * (1500 + omitted)), full=False).text
    assert f' ... (+{shown} chars, --full)"' in out


def test_full_shows_every_field_and_never_truncates():
    result = minimal_task("word " * 660)
    result.payload["created_by"] = "Ravi P"
    result.payload["comments"][0]["edited"] = "2026-10-19"
    out = render(result, full=True).text
    assert "word " * 659 + "word" in out
    assert "(+" not in out
    assert "created_by: Ravi P" in out
    assert "comments[1]{by,at,text,edited}:" in out


def test_html_is_stripped_to_text_before_measuring():
    markup = "<b>a</b>" * 1000  # 8000 chars of markup, 1000 chars of text: under the limit
    out = render(minimal_task(markup), full=False).text
    assert f"description: {'a' * 1000}\n" in out + "\n"


def test_a_requested_slice_with_no_rows_says_zero_results():
    result = Result(
        entity="task",
        payload={"key": "PROJ1-T1000", "title": "Fix login redirect loop", "comments": []},
    )
    assert render(result).text == (
        "task: PROJ1-T1000\ntitle: Fix login redirect loop\ncomments: 0 results"
    )


def test_full_list_shows_every_column_any_row_has():
    result = Result(
        entity="task",
        payload=[{"key": "K-1", "title": "A", "status": "Open", "owner": "X", "due": "2026-10-30"},
                 {"key": "K-2", "title": "B", "status": "Open", "owner": "Y"}],
    )
    assert render(result, full=True).text == (
        "tasks[2]{key,title,status,owner,due}:\n  K-1,A,Open,X,2026-10-30\n  K-2,B,Open,Y,null"
    )


def test_partial_failure_is_a_warnings_block_and_still_exit_0():
    result = Result(
        entity="task",
        payload={"key": "PROJ1-T1000", "title": "Fix login redirect loop", "status": "In Progress"},
        counts={"comments": 7, "history": 4},
        warnings=[
            "history: task not on the fetched page, 100 rows scanned",
            "attachments: Zoho returned 500, skipped",
        ],
        help=[Hint("task", {"ref": "PROJ1-T1000", "history": True}, positional=("ref",))],
    )
    out = render(result)
    assert out.text == golden("warnings.txt")
    assert out.exit_code == 0


def test_list_cut_by_the_command_says_how_many_more_and_how_to_page():
    result = Result(
        entity="task",
        payload=[{"key": "PROJ1-T1", "title": "Add SSO", "status": "Open", "owner": "Ravi P"}],
        counts={"total": 55},
        truncations=[Truncation("tasks", omitted=54, unit="rows", expand={"page": 2})],
    )
    assert render(result, mode="cli").text == (
        "counts: total=55\n"
        "tasks[1]{key,title,status,owner}:\n"
        "  PROJ1-T1,Add SSO,Open,Ravi P\n"
        'truncated[1]: "tasks (+54 rows, --page 2)"'
    )
    assert render(result, mode="mcp").text.endswith('truncated[1]: "tasks (+54 rows, page=2)"')


def test_cli_hints_are_copy_pasteable_commands():
    result = Result(
        entity="task",
        payload=[],
        help=[
            # None/False options are dropped; underscores become hyphens; values with spaces are quoted
            Hint("tasks", {"status": "In Progress", "limit": 5, "fresh": False, "owner": None}),
            Hint("task", {"ref": "PROJ1-T1", "max_comments": 3}, positional=("ref",)),
        ],
    )
    assert render(result, mode="cli").text == (
        "tasks: 0 results\n"
        "help[2]{run}:\n"
        "  zoho-axi tasks --status 'In Progress' --limit 5\n"
        "  zoho-axi task PROJ1-T1 --max-comments 3"
    )


def test_mcp_hints_are_tool_call_hints_with_named_params():
    result = Result(
        entity="task",
        payload=[],
        help=[
            Hint("task", {"ref": "PROJ1-T1", "comments": True, "full": True}, positional=("ref",)),
            Hint("tasks", {"status": "In Progress", "limit": 5, "fresh": False}),
        ],
    )
    assert render(result, mode="mcp").text == (
        "tasks: 0 results\n"
        "help[2]{run}:\n"
        "  task ref=PROJ1-T1 comments=true full=true\n"
        "  tasks status='In Progress' limit=5"
    )


def test_json_detail_is_the_same_information_as_the_toon_golden():
    out = render(task_detail_result(), mode="cli", fmt="json")
    assert out.exit_code == 0
    assert json.loads(out.text) == {  # the facts of tests/golden/render/detail.txt
        "task": "PROJ1-T1000",
        "title": "Fix login redirect loop",
        "status": "In Progress",
        "owner": "Asha K",
        "priority": "High",
        "due": "2026-10-30",
        "tasklist": "Auth",
        "counts": {"comments": 7, "subtasks": 2, "attachments": 3, "history": 4},
        "description": "Users bounce between /login and /home after the OAuth callback.",
        "comments": [
            {"by": "Asha K", "at": "2026-10-18", "text": "Repro on staging only"},
            {"by": "Ravi P", "at": "2026-10-19", "text": "Cleared the cookie jar and it still loops"},
        ],
        "help": [
            "zoho-axi task PROJ1-T1000 --comments --full",
            "zoho-axi task PROJ1-T1000 --history",
            "zoho-axi task PROJ1-T1000 --images",
        ],
    }


def test_json_empty_list_and_truncated_text_keep_their_signals():
    empty = json.loads(render(Result(entity="task", payload=[], help=[Hint("tasks", {"status": "all"})]),
                              fmt="json").text)
    assert empty == {"tasks": [], "help": ["zoho-axi tasks --status all"]}
    cut = json.loads(render(minimal_task("a" * 1501), fmt="json", mode="mcp").text)
    assert cut["description"] == "a" * 1500 + " ... (+1 chars, full=true)"


def _leaves(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from _leaves(value)
    elif node is not None:
        yield node


@pytest.mark.parametrize("result, full", [
    (task_detail_result(), False),
    (minimal_task("word " * 660, comment_text="ab" * 360), False),
    (minimal_task("<p>one</p><p>two</p>"), True),
    (Result(entity="task", payload=[{"key": "K-1", "title": "T", "status": "Open", "owner": "A"}],
            counts={"total": 9, "open": 4}, warnings=["slice failed"],
            truncations=[Truncation("tasks", 8, expand={"page": 2})],
            help=[Hint("task", {"ref": "K-1"}, positional=("ref",))]), False),
])
def test_toon_and_json_carry_the_same_information(result, full):
    toon_text = render(result, full=full).text
    json_text = render(result, full=full, fmt="json").text
    for leaf in _leaves(json.loads(json_text)):
        # every value in the JSON appears in the TOON (newlines are escaped there; counts become k=v)
        needle = json.dumps(leaf)[1:-1] if isinstance(leaf, str) else str(leaf)
        assert needle.replace('"', '\\"') in toon_text or needle in toon_text, leaf
    for key in json.loads(json_text):
        assert key in toon_text


def test_json_error_has_the_same_fields_and_exit_code():
    exc = _NotFound("not_found.task", "no such task", "try zoho-axi tasks")
    out = render_error(exc, fmt="json")
    assert json.loads(out.text) == {"error": "not_found.task", "message": "no such task",
                                    "hint": "try zoho-axi tasks"}
    assert out.exit_code == 4


def test_json_is_compact_utf8():
    out = render(minimal_task("café"), fmt="json").text
    assert "café" in out and "\n" not in out


@pytest.mark.parametrize("kwargs", [{"mode": "xml"}, {"fmt": "yaml"}])
def test_unknown_mode_or_format_is_a_programming_error(kwargs):
    with pytest.raises(ValueError):
        render(Result(entity="task", payload=[]), **kwargs)


class _NotFound(ZohoAxiError):  # stand-in for T02's NotFoundError: render maps on exit_code only
    exit_code = 4


class _RateLimited(ZohoAxiError):  # stand-in for T02's RateLimitedError
    exit_code = 6


def test_error_is_structured_toon_with_code_message_and_hint():
    exc = _NotFound(
        "not_found.task",
        "Task PROJ1-T9999 was not found in project PROJ1",
        "Check the key, or run zoho-axi tasks to list tasks",
    )
    out = render_error(exc)
    assert out.text == golden("error.txt")
    assert out.exit_code == 4


@pytest.mark.parametrize("exc, exit_code", [
    (UsageError("usage.unknown_flag", "unknown flag --stauts"), 2),
    (ConfigError("config.missing", "client_id is not set"), 3),
    (AuthError("auth.refresh_failed", "token refresh failed"), 3),
    (_NotFound("not_found.task", "no such task"), 4),
    (UpstreamError("upstream.timeout", "Zoho timed out"), 5),
    (_RateLimited("rate_limited", "too many requests"), 6),
    (ZohoAxiError("other", "something else"), 1),
])
def test_exit_codes_follow_the_overview_table(exc, exit_code):
    assert render_error(exc).exit_code == exit_code


def test_error_without_hint_has_no_hint_line_and_unknown_flag_is_exit_2():
    out = render_error(UsageError("usage.unknown_flag", "unknown flag --stauts"))
    assert out.text == "error: usage.unknown_flag\nmessage: unknown flag --stauts"
    assert out.exit_code == 2


def test_unexpected_exception_is_a_structured_exit_1_error_without_a_traceback():
    out = render_error(KeyError("boom"))
    assert out.text == "error: internal_error\nmessage: \"KeyError: 'boom'\""
    assert out.exit_code == 1


def test_html_blocks_and_entities_become_plain_lines():
    html = "<p>Fails on &amp; after login</p><ul><li>one</li><li>two&nbsp;</li></ul><script>x()</script>"
    out = render(minimal_task(html), full=False).text
    assert 'description: "Fails on & after login\\n- one\\n- two"' in out


def _description(html, fmt="json"):
    return json.loads(render(minimal_task(html), fmt=fmt).text)["description"]


def test_table_cells_are_separated_and_rows_are_lines():
    html = ("<table><tr><th>Browser</th><th>Version</th><th>Works</th></tr>"
            "<tr><td>Firefox</td><td>128</td><td>yes</td></tr>"
            "<tr><td>Safari</td><td></td><td>no</td></tr></table>")
    assert _description(html) == "Browser | Version | Works\nFirefox | 128 | yes\nSafari | | no"


def test_adjacent_cells_never_run_together_even_without_markup_whitespace():
    assert _description("<table><tr><td>Firefox</td><td>128</td><td>2026.03.1</td><td>yes</td></tr></table>") \
        == "Firefox | 128 | 2026.03.1 | yes"


def verbatim_diff(*lines):
    return Result("hooks", {"file": "/h/settings.json", "status": "dry run", "diff": [{"text": t} for t in lines]})


def test_verbatim_rows_keep_indentation_entities_and_tag_like_text():
    row = '+      "command": "echo  <module> &amp; <KEY>  x"'  # two-space runs, a tag-like token and an entity
    doc = json.loads(render(verbatim_diff(" {", row), fmt="json").text)
    assert [r["text"] for r in doc["diff"]] == [" {", row]


def test_non_verbatim_rows_are_still_stripped_and_collapsed():
    result = Result("comment", [{"by": "A", "at": "2026-10-18", "text": "<p>one   &amp;  two</p>"}])
    assert json.loads(render(result, fmt="json").text)["comments"][0]["text"] == "one & two"
