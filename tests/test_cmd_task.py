"""`zoho-projects-axi task <KEY|ID>` (T08). The seam is the product seam: argv/env in, (exit code, stdout, stderr) out,
with Zoho replayed below it from the synthetic fixtures (tests/fixtures/zoho) through the real gateway and
the real cache (one per test, in tmp_path). Expected outputs are literals or hand-checked goldens under
tests/golden/task, derived from the T08 ticket's illustrative output, never recomputed from the code.

Request budget (documented in zoho_projects_axi/core/commands/task.py): a numeric id costs 3 cold requests (task,
comments, attachments), a key costs 4 (one Search call more) and a warm repeat costs 0.
"""
import copy
import io
import json

import pytest

from support.factories import make_gateway
from support.fixtures import PORTAL, PROJECT, TASK, TASK_PARENT, first_json, replay_for
from support.golden import assert_golden
from support.replay import ReplayTransport
from support.tokens import assert_token_budget

from zoho_projects_axi.cli.main import main

BASE = f"https://projectsapi.zoho.com/api/v3/portal/{PORTAL}/projects/{PROJECT}"
LEGACY = f"https://projectsapi.zoho.com/restapi/portal/{PORTAL}/projects/{PROJECT}"
DEFAULT_SET = ("tasks/detail", "task_comments/v3_ok", "task_attachments/image_and_non_image")
BUDGET_DEFAULT = 600      # tokens, from the ticket
BUDGET_COMMENTS = 900


def run(argv, tmp_path, replay=None, *, env_extra=None, today=None):
    """Run the CLI in-process. Returns (exit code, stdout, stderr, gateway); `gateway.request_count` is the
    number of requests that reached the (replayed) wire."""
    env = {
        "HOME": str(tmp_path),
        "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env"),
        "ZOHO_PROJECTS_AXI_PORTAL_ID": PORTAL,
        "ZOHO_PROJECTS_AXI_PROJECT_ID": PROJECT,
        "ZOHO_PROJECTS_AXI_CLIENT_ID": "cid", "ZOHO_PROJECTS_AXI_CLIENT_SECRET": "csecret", "ZOHO_PROJECTS_AXI_REFRESH_TOKEN": "rtoken",
        **(env_extra or {}),
    }
    gateway = make_gateway(tmp_path, replay if replay is not None else ReplayTransport())
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, env, out, err, gateway=gateway, today=today)
    return code, out.getvalue().rstrip("\n"), err.getvalue(), gateway


def test_default_view_by_numeric_id_is_lean_with_counts_and_next_steps(tmp_path):
    code, out, err, gateway = run(["task", TASK], tmp_path, replay_for(*DEFAULT_SET))
    assert (code, err) == (0, "")
    assert_golden("task/default", out)
    assert gateway.request_count == 3  # task, comments (count), attachments (count + images)


# --- helpers for the shapes the stock fixtures do not cover ------------------------------------------

def _ok(replay, url, body, **kw):
    return replay.get(url, json=body, **kw)


def big_task_replay(comments=30, description_chars=6000):
    """The largest task the ticket talks about: a long rich-text description and `comments` comments."""
    detail = copy.deepcopy(first_json("tasks/detail"))
    detail["description"] = "<div>" + ("Background paragraph about the checkout flow. " * (description_chars // 46)) + "</div>"
    template = first_json("task_comments/v3_ok")["comments"][0]
    page = {"comments": [], "page_info": {"has_next_page": False, "count": comments, "page": 1}}
    for i in range(comments):
        c = copy.deepcopy(template)
        c["id"] = str(1000 + i)
        c["created_time"] = f"2026-03-{1 + i % 28:02d}T09:00:00.000Z"
        c["comment"] = f"<div>Comment number {i + 1}. " + "Some discussion text about the validator. " * 12 + "</div>"
        page["comments"].append(c)
    replay = ReplayTransport()
    _ok(replay, f"{BASE}/tasks/{TASK}", detail)
    _ok(replay, f"{BASE}/tasks/{TASK}/comments", page)
    return replay.get(f"{BASE}/tasks/{TASK}/attachments", status=204)


# --- request counts and the cache --------------------------------------------------------------------

def test_a_repeat_inside_the_ttl_costs_no_request_and_prints_the_same_thing(tmp_path):
    _, cold, _, g1 = run(["task", TASK], tmp_path, replay_for(*DEFAULT_SET))
    code, warm, _, g2 = run(["task", TASK], tmp_path, ReplayTransport())  # empty replay: any request raises
    assert (code, g1.request_count, g2.request_count) == (0, 3, 0)
    assert warm == cold


def test_fresh_bypasses_the_cache(tmp_path):
    run(["task", TASK], tmp_path, replay_for(*DEFAULT_SET))
    code, _, _, gateway = run(["task", TASK, "--fresh"], tmp_path, replay_for(*DEFAULT_SET))
    assert (code, gateway.request_count) == (0, 3)


def test_debug_prints_the_request_count_on_stderr_only(tmp_path):
    code, out, err, _ = run(["task", TASK, "--debug"], tmp_path, replay_for(*DEFAULT_SET))
    assert (code, err) == (0, "requests: 3\n")
    assert "requests" not in out


# --- key resolution ----------------------------------------------------------------------------------

def key_replay():
    return replay_for("search/hit", *DEFAULT_SET)


def test_a_human_key_is_resolved_with_one_search_call_then_the_same_view(tmp_path):
    _, by_id, _, _ = run(["task", TASK], tmp_path / "a", replay_for(*DEFAULT_SET))
    code, by_key, _, gateway = run(["task", "DM1-T2"], tmp_path / "b", key_replay())
    assert code == 0
    assert by_key == by_id
    assert gateway.request_count == 4  # search + task + comments + attachments


def test_a_resolved_key_is_memoized_so_the_warm_run_needs_no_request(tmp_path):
    run(["task", "DM1-T2"], tmp_path, key_replay())
    code, _, _, gateway = run(["task", "dm1-t2", "--comments"], tmp_path, ReplayTransport())
    assert (code, gateway.request_count) == (0, 0)


def test_fetching_by_id_teaches_the_key_so_a_later_key_lookup_skips_search(tmp_path):
    run(["task", TASK], tmp_path, replay_for(*DEFAULT_SET))
    expired = {"ZOHO_PROJECTS_AXI_TTL_TASK_SECONDS": "0", "ZOHO_PROJECTS_AXI_TTL_COMMENT_SECONDS": "0"}  # key memo has the long TTL
    code, _, _, gateway = run(["task", "DM1-T2"], tmp_path, replay_for(*DEFAULT_SET), env_extra=expired)
    assert code == 0
    assert gateway.request_count == 3  # task, comments, attachments: no search call


@pytest.mark.parametrize("miss", ["search/miss", "search/miss_empty_object"])
def test_an_unknown_key_is_a_structured_not_found_error_with_exit_4(tmp_path, miss):
    code, out, err, _ = run(["task", "NOPE-T999"], tmp_path, replay_for(miss))
    assert code == 4
    assert out.splitlines()[:2] == ["error: not_found", f"message: \"task 'NOPE-T999': not found in project {PROJECT}\""]
    assert out.splitlines()[2].startswith('hint: "Check the key.')
    assert "Traceback" not in out + err


def test_search_is_fuzzy_so_only_an_exact_key_match_counts(tmp_path):
    fuzzy = ReplayTransport().get(
        f"{LEGACY}/search", params={"search_term": "DM1-T", "module": "tasks"},
        json={"tasks": [{"id": 1, "key": "DM1-T20"}, {"id": 2, "key": "DM1-T21"}]})
    code, out, _, _ = run(["task", "DM1-T"], tmp_path, fuzzy)
    assert code == 4 and out.startswith("error: not_found")


def test_a_numeric_id_that_does_not_exist_is_exit_4_too(tmp_path):
    code, out, err, _ = run(["task", TASK], tmp_path, replay_for("tasks/detail_not_found"))
    assert code == 4
    assert out.splitlines()[0] == "error: not_found"
    assert "Traceback" not in out + err


def test_project_flag_changes_where_a_key_is_searched(tmp_path):
    other = "20000000099"
    replay = ReplayTransport().get(
        f"https://projectsapi.zoho.com/restapi/portal/{PORTAL}/projects/{other}/search",
        params={"search_term": "DM1-T2", "module": "tasks"}, json={"tasks": []})
    code, out, _, _ = run(["task", "DM1-T2", "--project", other], tmp_path, replay)
    assert code == 4
    assert f"not found in project {other}" in out


def test_no_project_configured_is_a_config_error_with_exit_3(tmp_path):
    code, out, _, _ = run(["task", "DM1-T2"], tmp_path, env_extra={"ZOHO_PROJECTS_AXI_PROJECT_ID": ""})
    assert code == 3
    assert out.splitlines()[0] == "error: config.project_missing"


# --- default view: budget, empty states, hints -------------------------------------------------------

def test_default_view_stays_under_budget_on_the_largest_task(tmp_path):
    code, out, _, gateway = run(["task", TASK], tmp_path, big_task_replay())
    assert code == 0
    assert gateway.request_count == 3
    assert "comments=30" in out
    assert "(+" in out and "--full" in out  # the 6000-char description was cut with a size hint
    assert_token_budget(out, BUDGET_DEFAULT, label="task default, 6k description + 30 comments")


def test_a_task_with_nothing_attached_shows_zero_counts_and_suggests_nothing(tmp_path):
    replay = ReplayTransport().get(f"{BASE}/tasks/{TASK}", json=first_json("tasks/detail"))
    replay.get(f"{BASE}/tasks/{TASK}/comments", status=204).get(f"{BASE}/tasks/{TASK}/attachments", status=204)
    code, out, _, _ = run(["task", TASK], tmp_path, replay)
    assert code == 0
    assert "counts: comments=0 subtasks=0 attachments=0" in out
    assert "images" not in out
    assert "help" not in out  # only slices with something in them are suggested


def test_help_suggests_subtasks_only_when_zoho_says_the_task_has_some(tmp_path):
    parent = ReplayTransport()
    parent.get(f"{BASE}/tasks/{TASK_PARENT}", json=first_json("tasks/detail_with_subtasks"))
    parent.get(f"{BASE}/tasks/{TASK_PARENT}/comments", status=204)
    parent.get(f"{BASE}/tasks/{TASK_PARENT}/attachments", status=204)
    code, out, _, _ = run(["task", TASK_PARENT], tmp_path, parent)
    assert code == 0
    assert "subtasks=yes" in out
    assert out.splitlines()[-2:] == ["help[1]{run}:", "  zoho-projects-axi task DM1-T5 --subtasks"]


def test_json_output_carries_the_same_fields(tmp_path):
    code, out, _, _ = run(["task", TASK, "--json"], tmp_path, replay_for(*DEFAULT_SET))
    doc = json.loads(out)
    assert code == 0
    assert (doc["task"], doc["title"], doc["status"], doc["owner"]) == ("DM1-T2", "Fix checkout validation",
                                                                         "In Progress", "Hugo Voss")
    assert doc["counts"] == {"comments": 2, "subtasks": 0, "attachments": 2, "images": 1}


def test_full_shows_the_whole_description_and_the_plain_fields_zoho_sent(tmp_path):
    code, out, _, _ = run(["task", TASK, "--full"], tmp_path, big_task_replay(comments=1))
    assert code == 0
    assert "(+" not in out
    assert "completion_percentage: 60" in out
    assert "created_time:" in out


def test_no_token_or_secret_ever_reaches_the_output(tmp_path):
    _, out, err, _ = run(["task", TASK, "--debug"], tmp_path, replay_for(*DEFAULT_SET))
    assert not {"tok-abc", "csecret", "rtoken"} & set(out.replace(":", " ").split() + err.split())


# --- usage errors ------------------------------------------------------------------------------------

def test_missing_ref_and_unknown_flags_are_usage_errors(tmp_path):
    assert run(["task"], tmp_path)[0] == 2
    code, out, _, _ = run(["task", TASK, "--comment"], tmp_path)
    assert code == 2
    assert "Did you mean --comments?" in out


def test_task_help_lists_the_slices_within_budget(tmp_path):
    code, out, _, _ = run(["task", "--help"], tmp_path)
    assert code == 0
    for flag in ("--comments", "--history", "--subtasks", "--attachments"):
        assert flag in out
    assert_token_budget(out, 250, label="task --help")


# --- slices ------------------------------------------------------------------------------------------

def test_comments_slice_shows_the_comments_with_author_date_and_plain_text(tmp_path):
    code, out, _, gateway = run(["task", TASK, "--comments"], tmp_path, replay_for(*DEFAULT_SET))
    assert code == 0
    assert gateway.request_count == 3  # the default's comments call is reused
    assert_golden("task/comments", out)
    assert "<div>" not in out and "<b>" not in out


def test_comments_from_the_legacy_api_read_the_same(tmp_path):
    legacy = replay_for("tasks/detail", "task_comments/v3_404_legacy_ok", "task_attachments/image_and_non_image")
    code, out, _, gateway = run(["task", TASK, "--comments"], tmp_path, legacy)
    lines = out.splitlines()
    assert code == 0
    assert gateway.request_count == 4  # the v3 404 plus the legacy answer count as two
    assert lines[lines.index("comments[2]{by,at,text}:") + 1:][:2] == [
        "  Bea Fixture,2026-03-03,Reproduced on staging.",
        "  Alex Sample,2026-03-04,Linking the design review.",
    ]


def test_comments_default_to_the_last_three_and_full_shows_all(tmp_path):
    code, out, _, _ = run(["task", TASK, "--comments"], tmp_path / "a", big_task_replay(30))
    rows = [ln for ln in out.splitlines() if ln.startswith("  ") and "Comment number" in ln]
    assert code == 0
    assert [ln.split("Comment number ")[1].split(".")[0] for ln in rows] == ["28", "29", "30"]
    assert "comments[3]{by,at,text}:" in out
    assert "comments (+27 older, --full)" in out
    assert out.splitlines()[-1] == "  zoho-projects-axi task DM1-T2 --comments --full"
    assert_token_budget(out, BUDGET_COMMENTS, label="task --comments, 30 comments")

    code, full, _, _ = run(["task", TASK, "--comments", "--full"], tmp_path / "b", big_task_replay(30))
    assert code == 0
    assert "comments[30]{by,at,text}:" in full
    assert "(+" not in full  # nothing is cut under --full


def test_attachments_slice_lists_name_type_and_size_only(tmp_path):
    code, out, _, gateway = run(["task", TASK, "--attachments"], tmp_path, replay_for(*DEFAULT_SET))
    assert code == 0 and gateway.request_count == 3
    assert_golden("task/attachments", out)
    assert "download" not in out and "http" not in out  # no URL; images are T12


def test_attachments_in_the_legacy_shape_count_images_by_type_or_extension(tmp_path):
    legacy = replay_for("tasks/detail", "task_comments/empty_204", "task_attachments/v3_404_legacy_upper_case")
    code, out, _, _ = run(["task", TASK, "--attachments"], tmp_path, legacy)
    assert code == 0
    assert "counts: comments=0 subtasks=0 attachments=4 images=3" in out  # png, octet-stream .JPG, webp
    assert "  screenshot-2.JPG,application/octet-stream," in out


def test_history_slice_shows_only_this_tasks_changes_from_the_unfiltered_page(tmp_path):
    replay = replay_for(*DEFAULT_SET)
    replay.add("GET", f"{BASE}/taskstatushistory", json=first_json("task_status_history/unfiltered_page"))
    code, out, _, gateway = run(["task", TASK, "--history"], tmp_path, replay)
    assert code == 0
    assert gateway.request_count == 4
    assert_golden("task/history", out)
    for other in ("Prepare release notes", "Archive old campaign", "Closed"):
        assert other not in out


def test_history_when_the_task_is_not_on_the_page_is_a_warning_not_noise(tmp_path):
    replay = replay_for(*DEFAULT_SET)
    replay.add("GET", f"{BASE}/taskstatushistory", json=first_json("task_status_history/target_absent"))
    code, out, _, _ = run(["task", TASK, "--history"], tmp_path, replay)
    assert code == 0
    assert "history[" not in out
    assert f"history: task {TASK} is not on the page Zoho returned" in out
    assert "Archive old campaign" not in out and "Prepare release notes" not in out
    assert "counts: comments=2 subtasks=0 attachments=2 images=1" in out


def test_subtasks_are_not_requested_when_zoho_says_there_are_none(tmp_path):
    code, out, _, gateway = run(["task", TASK, "--subtasks"], tmp_path, replay_for(*DEFAULT_SET))
    assert code == 0
    assert gateway.request_count == 3  # no ?parent_id= call: the replay has none and would raise
    assert "subtasks: 0 results" in out
    assert "warnings" not in out


def parent_replay():
    replay = replay_for("tasks/detail_with_subtasks")
    replay.get(f"{BASE}/tasks/{TASK_PARENT}/comments", status=204)
    return replay.get(f"{BASE}/tasks/{TASK_PARENT}/attachments", status=204)


def test_subtasks_are_flagged_unverified_and_limited_to_tasks_naming_this_parent(tmp_path):
    code, out, _, _ = run(["task", TASK_PARENT, "--subtasks"], tmp_path, parent_replay())
    assert code == 0
    assert_golden("task/subtasks", out)
    assert "Prepare release notes" not in out  # on the unfiltered page, but not this task's child


def test_a_failing_comments_call_is_a_warning_and_the_command_still_succeeds(tmp_path):
    replay = ReplayTransport().get(f"{BASE}/tasks/{TASK}", json=first_json("tasks/detail"))
    replay.get(f"{BASE}/tasks/{TASK}/comments", status=500, json={"error": {"message": "boom"}})
    replay.get(f"{BASE}/tasks/{TASK}/attachments", status=204)
    code, out, err, _ = run(["task", TASK, "--comments"], tmp_path, replay)
    lines = out.splitlines()
    assert (code, err) == (0, "")
    assert lines[0] == "task: DM1-T2"
    assert "counts: subtasks=0 attachments=0" in out  # no comments number: it could not be counted
    assert "comments[" not in out
    assert lines[lines.index("warnings[1]{message}:") + 1].startswith('  "comments unavailable: ')
    assert "HTTP 500" in out


@pytest.mark.parametrize("flag, suffix, status, reason", [
    ("--comments", "comments", 500, "Zoho returned HTTP 500"),
    ("--comments", "comments", 404, "not found"),
    ("--attachments", "attachments", 403, "forbidden (HTTP 403)"),
])
def test_a_sub_fetch_warning_is_short_and_human_without_ids_or_gateway_labels(tmp_path, flag, suffix, status, reason):
    replay = ReplayTransport().get(f"{BASE}/tasks/{TASK}", json=first_json("tasks/detail"))
    replay.get(f"{BASE}/tasks/{TASK}/comments", status=status if suffix == "comments" else 204)
    replay.get(f"{BASE}/tasks/{TASK}/attachments", status=status if suffix == "attachments" else 204)
    if status == 404:  # the gateway retries a v3 404 on the legacy path before giving up
        replay.get(f"{LEGACY}/tasks/{TASK}/{suffix}/", status=404)
    code, out, _, _ = run(["task", TASK, flag], tmp_path, replay)
    assert code == 0
    assert f'  "{suffix} unavailable: {reason}"' in out.splitlines()
    for leaked in ("portal=", "project=", "task_comments", "task_attachments", f"task={TASK}"):
        assert leaked not in out


def test_a_forbidden_attachments_call_is_a_warning_too(tmp_path):
    replay = ReplayTransport().get(f"{BASE}/tasks/{TASK}", json=first_json("tasks/detail"))
    replay.get(f"{BASE}/tasks/{TASK}/comments", status=204)
    replay.get(f"{BASE}/tasks/{TASK}/attachments", status=403)
    code, out, _, _ = run(["task", TASK, "--attachments"], tmp_path, replay)
    assert code == 0
    assert "attachments unavailable: " in out and "forbidden" in out


def test_a_rejected_token_is_not_swallowed_by_the_partial_failure_rule(tmp_path):
    replay = ReplayTransport().get(f"{BASE}/tasks/{TASK}", json=first_json("tasks/detail"))
    replay.get(f"{BASE}/tasks/{TASK}/comments", status=401)
    code, out, _, _ = run(["task", TASK], tmp_path, replay)
    assert code == 3
    assert out.splitlines()[0] == "error: auth.unauthorized"
