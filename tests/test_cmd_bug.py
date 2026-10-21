"""`zoho-projects-axi bug <KEY|ID>` (T09). Same seam as test_cmd_task.py: argv/env in, (exit code, stdout, stderr) out, Zoho
replayed below the CLI from the synthetic fixtures (tests/fixtures/zoho) through the real gateway and cache.
Expected outputs are literals or hand-checked goldens under tests/golden/bug, never recomputed from the code.

Request budget (zoho_projects_axi/core/commands/bug.py): a numeric id costs 3 cold requests (bug, comments, attachments),
a key costs 4 (one Search call more), `--task` one more when the bug links a task, a warm repeat costs 0.
"""
import copy
import io
import json

import pytest

from support.factories import make_gateway
from support.fixtures import BUG, BUG_NO_TASK, PORTAL, PROJECT, TASK, first_json, replay_for
from support.golden import assert_golden
from support.replay import ReplayTransport
from support.tokens import assert_token_budget

from zoho_projects_axi.cli.main import main

BASE = f"https://projectsapi.zoho.com/api/v3/portal/{PORTAL}/projects/{PROJECT}"
LEGACY = f"https://projectsapi.zoho.com/restapi/portal/{PORTAL}/projects/{PROJECT}"
DEFAULT_SET = ("bugs/detail_with_task", "bug_comments/v3_ok", "bug_attachments/v3_nested_file_info")
BUDGET_DEFAULT = 500      # tokens, from the ticket
BUDGET_COMMENTS = 900


def run(argv, tmp_path, replay=None, *, env_extra=None, today=None):
    """Run the CLI in-process. Returns (exit code, stdout, stderr, gateway)."""
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
    code, out, err, gateway = run(["bug", BUG], tmp_path, replay_for(*DEFAULT_SET))
    assert (code, err) == (0, "")
    assert_golden("bug/default", out)
    assert gateway.request_count == 3  # bug, comments (count), attachments (count + images)


# --- key resolution: Search answers bugs in two documented-vs-proven shapes --------------------------

@pytest.mark.parametrize("search", ["search/bug_hit_code_shape", "search/bug_hit_docs_shape"])
def test_a_bug_key_is_resolved_with_one_search_call_whichever_wrapper_zoho_uses(tmp_path, search):
    # code shape: {"bugs": [{id, key}]}; docs shape: {"issue": [{issue_id, issue_id_str, prefix}]}
    _, by_id, _, _ = run(["bug", BUG], tmp_path / "a", replay_for(*DEFAULT_SET))
    code, by_key, _, gateway = run(["bug", "DM1-I1"], tmp_path / "b", replay_for(search, *DEFAULT_SET))
    assert code == 0
    assert by_key == by_id
    assert gateway.request_count == 4  # search + bug + comments + attachments


def test_a_resolved_bug_key_is_memoized_so_the_warm_run_needs_no_request(tmp_path):
    run(["bug", "DM1-I1"], tmp_path, replay_for("search/bug_hit_docs_shape", *DEFAULT_SET))
    code, _, _, gateway = run(["bug", "dm1-i1", "--comments"], tmp_path, ReplayTransport())
    assert (code, gateway.request_count) == (0, 0)


@pytest.mark.parametrize("miss", ["search/miss", "search/miss_empty_object"])
def test_an_unknown_bug_key_is_a_structured_not_found_error_with_exit_4(tmp_path, miss):
    # the miss fixtures answer module=tasks; the bug search asks module=bugs, so register the same bodies for it
    body = first_json(miss)
    replay = ReplayTransport().get(f"{LEGACY}/search", params={"search_term": "NOPE-I9", "module": "bugs"}, json=body)
    code, out, err, _ = run(["bug", "NOPE-I9"], tmp_path, replay)
    assert code == 4
    assert out.splitlines()[:2] == ["error: not_found", f"message: \"bug 'NOPE-I9': not found in project {PROJECT}\""]
    assert "Traceback" not in out + err


def test_search_is_fuzzy_so_only_an_exact_bug_key_counts(tmp_path):
    fuzzy = ReplayTransport().get(
        f"{LEGACY}/search", params={"search_term": "DM1-I", "module": "bugs"},
        json={"issue": [{"issue_id": 1, "issue_id_str": "1", "prefix": "DM1-I20"},
                        {"issue_id": 2, "issue_id_str": "2", "prefix": "DM1-I21"}]})
    code, out, _, _ = run(["bug", "DM1-I"], tmp_path, fuzzy)
    assert code == 4 and out.startswith("error: not_found")


def test_a_numeric_bug_id_that_does_not_exist_is_exit_4_too(tmp_path):
    missing = ReplayTransport().get(f"{BASE}/bugs/{BUG}", status=404, json={"error": {"title": "RESOURCE_NOT_FOUND"}})
    code, out, err, _ = run(["bug", BUG], tmp_path, missing)
    assert code == 4
    assert out.splitlines()[0] == "error: not_found"
    assert "Traceback" not in out + err


def test_fetching_by_id_teaches_the_key_so_a_later_key_lookup_skips_search(tmp_path):
    run(["bug", BUG], tmp_path, replay_for(*DEFAULT_SET))
    expired = {"ZOHO_PROJECTS_AXI_TTL_TASK_SECONDS": "0", "ZOHO_PROJECTS_AXI_TTL_COMMENT_SECONDS": "0"}  # key memo has the long TTL
    code, _, _, gateway = run(["bug", "DM1-I1"], tmp_path, replay_for(*DEFAULT_SET), env_extra=expired)
    assert code == 0
    assert gateway.request_count == 3  # bug, comments, attachments: no search call


# --- helpers for the shapes the stock fixtures do not cover ------------------------------------------

def big_bug_replay(comments=30, description_chars=6000):
    """The largest bug the ticket talks about (synthetic): a long rich-text description and `comments` comments.
    `description` is invented on the v3 detail (the documented issue object lists none; the legacy bug has one)."""
    detail = copy.deepcopy(first_json("bugs/detail_with_task"))
    detail["description"] = "<div>" + ("Background paragraph about the checkout flow. " * (description_chars // 46)) + "</div>"
    template = first_json("bug_comments/v3_ok")["comments"][0]
    page = {"comments": [], "page_info": {"has_next_page": False, "count": comments, "page": 1}}
    for i in range(comments):
        c = copy.deepcopy(template)
        c["id"] = str(1000 + i)
        c["created_time"] = f"2026-03-{1 + i % 28:02d}T09:00:00.000Z"
        c["comment"] = f"<div>Comment number {i + 1}. " + "Some discussion text about the validator. " * 12 + "</div>"
        page["comments"].append(c)
    replay = ReplayTransport()
    replay.get(f"{BASE}/bugs/{BUG}", json=detail)
    replay.get(f"{BASE}/bugs/{BUG}/comments", json=page)
    return replay.get(f"{BASE}/bugs/{BUG}/attachments", status=204)


def bug_replay(detail, *, comments_status=204, attachments_status=204):
    replay = ReplayTransport().get(f"{BASE}/bugs/{BUG}", json=detail)
    replay.get(f"{BASE}/bugs/{BUG}/comments", status=comments_status)
    return replay.get(f"{BASE}/bugs/{BUG}/attachments", status=attachments_status)


# --- slices ------------------------------------------------------------------------------------------

def test_comments_slice_shows_author_date_and_plain_text(tmp_path):
    code, out, _, gateway = run(["bug", BUG, "--comments"], tmp_path, replay_for(*DEFAULT_SET))
    assert code == 0
    assert gateway.request_count == 3  # the default's comments call is reused
    assert_golden("bug/comments", out)


def test_comments_default_to_the_last_three_and_full_shows_all(tmp_path):
    code, out, _, _ = run(["bug", BUG, "--comments"], tmp_path / "a", big_bug_replay(30))
    rows = [ln for ln in out.splitlines() if ln.startswith("  ") and "Comment number" in ln]
    assert code == 0
    assert [ln.split("Comment number ")[1].split(".")[0] for ln in rows] == ["28", "29", "30"]
    assert "comments (+27 older, --full)" in out
    assert "  zoho-projects-axi bug DM1-I1 --comments --full" in out.splitlines()
    assert_token_budget(out, BUDGET_COMMENTS, label="bug --comments, 30 comments")

    code, full, _, _ = run(["bug", BUG, "--comments", "--full"], tmp_path / "b", big_bug_replay(30))
    assert code == 0
    assert "comments[30]{by,at,text}:" in full
    assert "(+" not in full


def test_attachments_slice_lists_name_type_and_size_only(tmp_path):
    code, out, _, gateway = run(["bug", BUG, "--attachments"], tmp_path, replay_for(*DEFAULT_SET))
    assert code == 0 and gateway.request_count == 3
    assert_golden("bug/attachments", out)
    assert "download" not in out and "http" not in out  # no URL; images are T12


def test_attachments_from_the_legacy_wrapper_read_the_same(tmp_path):
    legacy = replay_for("bugs/detail_with_task", "bug_comments/v3_ok", "bug_attachments/v3_404_legacy_attachment_details")
    code, out, _, gateway = run(["bug", BUG, "--attachments"], tmp_path, legacy)
    lines = out.splitlines()
    assert code == 0
    assert gateway.request_count == 4  # the v3 404 plus the legacy answer count as two
    assert "counts: comments=1 attachments=2 images=1" in out
    assert lines[lines.index("attachments[2]{name,type,size}:") + 1:][:2] == [
        "  checkout-500.png,image/png,179 KB",
        "  SessionValidation.java,text/x-java,3 KB",
    ]


def test_task_slice_summarizes_the_linked_task_with_one_extra_request(tmp_path):
    # the bugs/detail_with_task fixture also carries the linked task's detail as its second entry
    code, out, _, gateway = run(["bug", BUG, "--task"], tmp_path, replay_for(*DEFAULT_SET))
    assert code == 0
    assert gateway.request_count == 4
    assert_golden("bug/task", out)


def test_task_slice_survives_a_failing_task_fetch_with_a_warning_and_the_embedded_summary(tmp_path):
    replay = bug_replay(first_json("bugs/detail_with_task"), comments_status=204)
    replay.add("GET", f"{BASE}/tasks/{TASK}", status=500, json={"error": {"message": "boom"}})
    code, out, _, _ = run(["bug", BUG, "--task"], tmp_path, replay)
    lines = out.splitlines()
    assert code == 0
    assert lines[lines.index("linked_task[1]{key,title,status,owner}:") + 1] == '  DM1-T2,Fix checkout validation,"",""'
    assert any(ln.startswith('  "linked task unavailable: ') for ln in lines)


# --- a bug with and without a linked task ------------------------------------------------------------

def no_task_replay():
    replay = ReplayTransport().get(f"{BASE}/bugs/{BUG_NO_TASK}", json=first_json("bugs/detail_without_task"))
    replay.get(f"{BASE}/bugs/{BUG_NO_TASK}/comments", status=204)
    return replay.get(f"{BASE}/bugs/{BUG_NO_TASK}/attachments", status=204)


def test_a_bug_without_a_linked_task_has_no_task_line_and_no_task_hint(tmp_path):
    code, out, err, _ = run(["bug", BUG_NO_TASK], tmp_path, no_task_replay())
    assert (code, err) == (0, "")
    assert_golden("bug/no_task", out)


def test_task_slice_without_a_link_says_so_in_a_warning_instead_of_guessing_an_endpoint(tmp_path):
    code, out, _, gateway = run(["bug", BUG_NO_TASK, "--task"], tmp_path, no_task_replay())
    assert code == 0
    assert gateway.request_count == 3  # nothing was requested for the task
    assert "linked_task" not in out
    assert "no `associated_task` field" in out and "unverified" in out


@pytest.mark.parametrize("value", [None, {}, [], ""])
def test_a_null_or_empty_associated_task_is_the_same_as_absent(tmp_path, value):
    detail = copy.deepcopy(first_json("bugs/detail_without_task"))
    detail["id"] = BUG
    detail["associated_task"] = value
    code, out, _, _ = run(["bug", BUG, "--task"], tmp_path, bug_replay(detail))
    assert code == 0
    assert "no `associated_task` field" in out
    assert "\ntask:" not in out


def test_a_bare_id_for_associated_task_still_leads_to_the_task_summary(tmp_path):
    detail = copy.deepcopy(first_json("bugs/detail_with_task"))
    detail["associated_task"] = TASK
    replay = bug_replay(detail).get(f"{BASE}/tasks/{TASK}", json=first_json("tasks/detail"))
    code, out, _, _ = run(["bug", BUG, "--task"], tmp_path, replay)
    assert code == 0
    assert "  DM1-T2,Fix checkout validation,In Progress,Hugo Voss" in out


# --- field spellings, budget, formats ----------------------------------------------------------------

def test_legacy_field_spellings_read_the_same_fields(tmp_path):
    replay = replay_for("bugs/detail_legacy_fields")
    replay.get(f"{BASE}/bugs/{BUG}/comments", status=204).get(f"{BASE}/bugs/{BUG}/attachments", status=204)
    code, out, _, _ = run(["bug", BUG], tmp_path, replay)
    assert code == 0
    assert out.splitlines()[:11] == [
        "bug: DM1-I1",
        "title: Postcode validation missing on checkout form",
        "status: Open",
        "severity: Show Stopper",
        "owner: Hugo Voss",
        "reporter: Hugo Okoye",
        "module: Checkout",
        "created: 2026-02-20",
        "due: 2026-03-31",
        "task: DM1-T2",
        "counts: comments=0 attachments=0",
    ]
    assert out.splitlines()[11] == "description: Entering 12-34 as the postcode is accepted and the payment step then fails."


def test_default_view_stays_under_budget_on_the_largest_bug(tmp_path):
    code, out, _, gateway = run(["bug", BUG], tmp_path, big_bug_replay())
    assert code == 0
    assert gateway.request_count == 3
    assert "comments=30" in out
    assert "(+" in out and "--full" in out  # the 6000-char description was cut with a size hint
    assert_token_budget(out, BUDGET_DEFAULT, label="bug default, 6k description + 30 comments")


def test_json_output_carries_the_same_fields(tmp_path):
    code, out, _, _ = run(["bug", BUG, "--json"], tmp_path, replay_for(*DEFAULT_SET))
    doc = json.loads(out)
    assert code == 0
    assert (doc["bug"], doc["title"], doc["status"], doc["severity"], doc["owner"], doc["task"]) == (
        "DM1-I1", "Postcode validation missing on checkout form", "Open", "Critical", "Hugo Voss", "DM1-T2")
    assert doc["counts"] == {"comments": 1, "attachments": 2, "images": 1}


def test_full_shows_the_whole_description_and_the_plain_fields_zoho_sent(tmp_path):
    code, out, _, _ = run(["bug", BUG, "--full"], tmp_path, big_bug_replay(comments=1))
    assert code == 0
    assert "(+" not in out
    assert "flag: Internal" in out  # a plain field the lean view leaves out
    assert "rate_per_hour: 0\n" in out  # TOON writes the float 0.0 as 0
    assert "last_updated_time: " in out and "2026-03-06T09:28:58.000Z" in out


# --- request counts, cache, scope --------------------------------------------------------------------

def test_a_repeat_inside_the_ttl_costs_no_request_and_prints_the_same_thing(tmp_path):
    _, cold, _, g1 = run(["bug", BUG], tmp_path, replay_for(*DEFAULT_SET))
    code, warm, _, g2 = run(["bug", BUG], tmp_path, ReplayTransport())  # empty replay: any request raises
    assert (code, g1.request_count, g2.request_count) == (0, 3, 0)
    assert warm == cold


def test_fresh_bypasses_the_cache(tmp_path):
    run(["bug", BUG], tmp_path, replay_for(*DEFAULT_SET))
    code, _, _, gateway = run(["bug", BUG, "--fresh"], tmp_path, replay_for(*DEFAULT_SET))
    assert (code, gateway.request_count) == (0, 3)


def test_debug_prints_the_request_count_on_stderr_only(tmp_path):
    code, out, err, _ = run(["bug", BUG, "--debug"], tmp_path, replay_for(*DEFAULT_SET))
    assert (code, err) == (0, "requests: 3\n")
    assert "requests" not in out


def test_project_flag_changes_where_a_bug_key_is_searched(tmp_path):
    other = "20000000099"
    replay = ReplayTransport().get(
        f"https://projectsapi.zoho.com/restapi/portal/{PORTAL}/projects/{other}/search",
        params={"search_term": "DM1-I1", "module": "bugs"}, json={"bugs": []})
    code, out, _, _ = run(["bug", "DM1-I1", "--project", other], tmp_path, replay)
    assert code == 4
    assert f"not found in project {other}" in out


def test_no_project_configured_is_a_config_error_with_exit_3(tmp_path):
    code, out, _, _ = run(["bug", "DM1-I1"], tmp_path, env_extra={"ZOHO_PROJECTS_AXI_PROJECT_ID": ""})
    assert code == 3
    assert out.splitlines()[0] == "error: config.project_missing"


def test_no_token_or_secret_ever_reaches_the_output(tmp_path):
    _, out, err, _ = run(["bug", BUG, "--debug"], tmp_path, replay_for(*DEFAULT_SET))
    assert not {"tok-abc", "csecret", "rtoken"} & set(out.replace(":", " ").split() + err.split())


# --- empty states, partial failure -------------------------------------------------------------------

def test_a_bug_with_nothing_attached_shows_zero_counts_and_suggests_nothing(tmp_path):
    code, out, _, _ = run(["bug", BUG_NO_TASK], tmp_path, no_task_replay())
    assert code == 0
    assert "counts: comments=0 attachments=0" in out
    assert "images" not in out and "help" not in out


def test_a_failing_comments_call_is_a_warning_and_the_command_still_succeeds(tmp_path):
    replay = ReplayTransport().get(f"{BASE}/bugs/{BUG}", json=first_json("bugs/detail_with_task"))
    replay.get(f"{BASE}/bugs/{BUG}/comments", status=500, json={"error": {"message": "boom"}})
    replay.get(f"{BASE}/bugs/{BUG}/attachments", status=204)
    code, out, err, _ = run(["bug", BUG, "--comments"], tmp_path, replay)
    lines = out.splitlines()
    assert (code, err) == (0, "")
    assert lines[0] == "bug: DM1-I1"
    assert "counts: attachments=0" in out  # no comments number: it could not be counted
    assert "comments[" not in out
    assert lines[lines.index("warnings[1]{message}:") + 1].startswith('  "comments unavailable: ')


def test_a_forbidden_attachments_call_is_a_warning_too(tmp_path):
    replay = bug_replay(first_json("bugs/detail_with_task"), attachments_status=403)
    code, out, _, _ = run(["bug", BUG, "--attachments"], tmp_path, replay)
    assert code == 0
    assert "attachments unavailable: " in out and "forbidden" in out


def test_a_rejected_token_is_not_swallowed_by_the_partial_failure_rule(tmp_path):
    replay = ReplayTransport().get(f"{BASE}/bugs/{BUG}", json=first_json("bugs/detail_with_task"))
    replay.get(f"{BASE}/bugs/{BUG}/comments", status=401)
    code, out, _, _ = run(["bug", BUG], tmp_path, replay)
    assert code == 3
    assert out.splitlines()[0] == "error: auth.unauthorized"


# --- usage errors ------------------------------------------------------------------------------------

def test_missing_ref_and_unknown_flags_are_usage_errors(tmp_path):
    assert run(["bug"], tmp_path)[0] == 2
    code, out, _, _ = run(["bug", BUG, "--comment"], tmp_path)
    assert code == 2
    assert "Did you mean --comments?" in out
    assert run(["bug", BUG, "--history"], tmp_path)[0] == 2  # a task slice, not a bug one


def test_bug_help_lists_the_slices_within_budget(tmp_path):
    code, out, _, _ = run(["bug", "--help"], tmp_path)
    assert code == 0
    for flag in ("--comments", "--attachments", "--task"):
        assert flag in out
    assert_token_budget(out, 250, label="bug --help")
