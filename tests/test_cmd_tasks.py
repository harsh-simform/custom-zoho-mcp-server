"""`zoho-axi tasks` (T10) at the product seam: argv in, rendered text + exit code + request count out.
Zoho is replayed below the gateway (tests/support/world.py); expected texts are hand-written literals.
Token budget uses the same len/4 proxy as tests/test_cli.py.
"""
import json

import pytest
from support.world import (
    P, PR, World, legacy_milestones, replay_of, task, tasklists_body, tasks_body, token_count,
    LEGACY_MILESTONES_URL,
)

TASKS_BUDGET = 500
PAGE = {}  # Zoho serves page 1 of 100 by default, so the plain call carries no paging params


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def three():
    return tasks_body(
        task(1, "Prepare release notes", "Open", "Alex Sample"),
        task(2, "Fix checkout validation", "In Progress", "Bea Fixture"),
        task(3, "Archive old assets", "Closed", "Alex Sample", closed=True),
    )


def test_tasks_lists_rows_with_totals_by_status(world):
    code, out, err = world.run(["tasks"], replay_of((f"{PR}/tasks", three(), PAGE)))
    assert code == 0
    assert out == "\n".join([
        "counts: matched=3 open=1 in_progress=1 closed=1",
        "tasks[3]{key,title,status,owner}:",
        "  DM1-T1,Prepare release notes,Open,Alex Sample",
        "  DM1-T2,Fix checkout validation,In Progress,Bea Fixture",
        "  DM1-T3,Archive old assets,Closed,Alex Sample",
        "help[1]{run}:",
        "  zoho-axi task DM1-T1",
    ])
    assert world.requests(err) == 1


def test_warm_second_call_makes_zero_requests_and_is_identical(world):
    cold = world.run(["tasks"], replay_of((f"{PR}/tasks", three(), PAGE)))
    warm = world.run(["tasks"])  # empty transport: any request would be an internal_error
    assert warm[:2] == cold[:2]
    assert world.requests(warm[2]) == 0


def test_json_flag_carries_the_same_rows(world):
    _, out, _ = world.run(["tasks", "--json"], replay_of((f"{PR}/tasks", three(), PAGE)))
    doc = json.loads(out)
    assert [t["key"] for t in doc["tasks"]] == ["DM1-T1", "DM1-T2", "DM1-T3"]
    assert doc["counts"] == {"matched": 3, "open": 1, "in_progress": 1, "closed": 1}


def test_unassigned_owner_renders_as_empty_not_zoho_placeholder(world):
    body = tasks_body(task(1, "Orphan", owner=None))
    _, out, _ = world.run(["tasks"], replay_of((f"{PR}/tasks", body, PAGE)))
    assert "Unassigned" not in out
    assert "  DM1-T1,Orphan,Open," in out


def test_status_filter_is_client_side_and_case_insensitive(world):
    code, out, _ = world.run(["tasks", "--status", "in progress"], replay_of((f"{PR}/tasks", three(), PAGE)))
    assert code == 0
    assert "counts: matched=1 in_progress=1" in out
    assert "DM1-T2" in out and "DM1-T1" not in out


def test_open_alias_means_not_closed(world):
    _, out, _ = world.run(["tasks", "--status", "open"], replay_of((f"{PR}/tasks", three(), PAGE)))
    assert "counts: matched=2 open=1 in_progress=1" in out
    assert "DM1-T3" not in out


def test_owner_filter_matches_names_case_insensitively(world):
    _, out, _ = world.run(["tasks", "--owner", "bea"], replay_of((f"{PR}/tasks", three(), PAGE)))
    assert "counts: matched=1 in_progress=1" in out
    assert "DM1-T2,Fix checkout validation,In Progress,Bea Fixture" in out


def test_empty_state_is_explicit_and_hints_a_broader_query(world):
    code, out, _ = world.run(["tasks", "--status", "blocked"], replay_of((f"{PR}/tasks", three(), PAGE)))
    assert code == 0
    assert out.splitlines()[:2] == ["counts: matched=0", "tasks: 0 results"]
    assert "zoho-axi tasks" in out.splitlines()[-1]


def test_over_limit_rows_are_cut_with_a_hint_to_raise_the_limit(world):
    body = tasks_body(*[task(n, f"Task number {n}") for n in range(1, 8)])
    _, out, _ = world.run(["tasks", "--limit", "3"], replay_of((f"{PR}/tasks", body, PAGE)))
    assert "tasks[3]{key,title,status,owner}:" in out
    assert "counts: matched=7 shown=3 open=7" in out
    assert 'truncated[1]: "tasks (+4 rows, --limit 7)"' in out


def test_default_limit_is_20(world):
    body = tasks_body(*[task(n, f"Task number {n}") for n in range(1, 26)])
    _, out, _ = world.run(["tasks"], replay_of((f"{PR}/tasks", body, PAGE)))
    assert "tasks[20]{key,title,status,owner}:" in out
    assert "tasks (+5 rows, --limit 25)" in out


def test_more_pages_on_the_server_produce_a_next_page_hint(world):
    body = tasks_body(task(1, "A"), has_next=True)
    _, out, _ = world.run(["tasks"], replay_of((f"{PR}/tasks", body, PAGE)))
    assert "zoho-axi tasks --page 2" in out


def test_page_flag_requests_that_page_from_zoho(world):
    params = {"page": "2"}
    code, out, _ = world.run(["tasks", "--page", "2"], replay_of((f"{PR}/tasks", tasks_body(task(1, "A")), params)))
    assert code == 0 and "DM1-T1" in out


def test_tasklist_id_becomes_the_server_side_filter_criterion(world):
    literal = '{"criteria": [{"field_name": "tasklist_id", "criteria_condition": "is", "value": ["9001"]}], "pattern": "1"}'
    params = {**PAGE, "filter": literal}
    code, out, err = world.run(["tasks", "--tasklist", "9001"], replay_of((f"{PR}/tasks", three(), params)))
    assert code == 0 and "matched=3" in out
    assert world.requests(err) == 1  # numeric id needs no lookup


def test_tasklist_name_resolves_through_the_index_and_is_free_when_warm(world):
    lists = tasklists_body(("9001", "Auth"), ("9002", "Billing"))
    filt = '{"criteria": [{"field_name": "tasklist_id", "criteria_condition": "is", "value": ["9002"]}], "pattern": "1"}'
    cold = replay_of((f"{PR}/tasklists", lists), (f"{PR}/tasks", three(), {**PAGE, "filter": filt}))
    code, out, err = world.run(["tasks", "--tasklist", "billing"], cold)
    assert code == 0 and world.requests(err) == 2
    warm = world.run(["tasks", "--tasklist", "Billing"])
    assert warm[:2] == (0, out) and world.requests(warm[2]) == 0


def test_unknown_tasklist_name_is_not_found_and_lists_candidates(world):
    code, out, _ = world.run(["tasks", "--tasklist", "Nope"], replay_of((f"{PR}/tasklists", tasklists_body(("9001", "Auth")))))
    assert code == 4
    assert out.splitlines()[0] == "error: not_found"
    assert "Auth" in out


def test_milestone_name_resolves_via_legacy_fallback_list(world):
    ms = legacy_milestones(("5001", "Beta Release", "04-30-2026 11:59 PM", False))
    filt = '{"criteria": [{"field_name": "milestone_id", "criteria_condition": "is", "value": ["5001"]}], "pattern": "1"}'
    replay = replay_of(
        (f"{PR}/milestones", {}, None, 404),
        (LEGACY_MILESTONES_URL, ms),
        (f"{PR}/tasks", three(), {**PAGE, "filter": filt}),
    )
    code, out, _ = world.run(["tasks", "--milestone", "beta"], replay)
    assert code == 0 and "matched=3" in out


def test_non_numeric_limit_is_a_usage_error(world):
    code, out, _ = world.run(["tasks", "--limit", "many"])
    assert code == 2
    assert out.splitlines()[0] == "error: usage.invalid_arguments"


def test_project_flag_scopes_the_request_to_that_project(world):
    other = f"{P}/projects/777/tasks"
    code, out, _ = world.run(["tasks", "--project", "777"], replay_of((other, tasks_body(task(1, "A")), PAGE)))
    assert code == 0 and "DM1-T1" in out


def test_without_a_project_it_is_a_config_error(tmp_path):
    code, out, _ = World(tmp_path, configured=False).run(["tasks"])
    assert code == 3
    assert out.splitlines()[0] == "error: config.project_missing"


def test_twenty_row_list_stays_inside_the_token_budget(world):
    titles = ["Fix login redirect loop on staging", "Prepare release notes for 2.4", "Migrate billing webhooks",
              "Add retry to export job", "Review accessibility audit findings"]
    statuses = ["Open", "In Progress", "In Review", "Closed"]
    owners = ["Alex Sample", "Bea Fixture", "Chidi Anagonye", ""]
    rows = [task(n, f"{titles[n % 5]} {n}", statuses[n % 4], owners[n % 4] or None, closed=n % 4 == 3)
            for n in range(1, 21)]
    _, out, _ = world.run(["tasks"], replay_of((f"{PR}/tasks", tasks_body(*rows), PAGE)))
    assert "tasks[20]" in out
    assert token_count(out) <= TASKS_BUDGET, token_count(out)


def test_legacy_shaped_tasks_are_read_too(world):
    # Invented-but-plausible legacy /restapi spellings: numeric id, `key`, `details.owners`, status.type, MM-dd-yyyy.
    body = {"tasks": [{"id": 3001, "key": "PROJ1-T1", "name": "Legacy task", "status": {"name": "Open", "type": "open"},
                       "details": {"owners": [{"name": "Dee Legacy", "id": "9"}]}, "end_date": "10-30-2026"},
                      {"id": 3002, "key": "PROJ1-T2", "name": "Done one", "status": {"name": "Closed", "type": "closed"},
                       "completed": True}]}
    _, out, _ = world.run(["tasks", "--status", "closed"], replay_of((f"{PR}/tasks", body, PAGE)))
    assert "counts: matched=1 closed=1" in out
    assert "  PROJ1-T2,Done one,Closed," in out
    _, out, _ = world.run(["tasks", "--owner", "dee"])  # warm
    assert "  PROJ1-T1,Legacy task,Open,Dee Legacy" in out


def test_string_booleans_from_zoho_do_not_count_as_closed(world):
    body = tasks_body(task(1, "A"))
    body["tasks"][0]["is_completed"] = "false"
    body["tasks"][0]["status"]["is_closed_type"] = "false"
    _, out, _ = world.run(["tasks", "--status", "open"], replay_of((f"{PR}/tasks", body, PAGE)))
    assert "matched=1" in out
