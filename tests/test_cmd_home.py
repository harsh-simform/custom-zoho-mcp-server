"""Live `home` (T10, AXI principle 8) at the product seam. Today is pinned to 2026-10-21 and "due soon"
means within 7 days (through 2026-10-28). Bodies are invented-but-plausible (tests/support/world.py)."""
import pytest
from support.world import P, PR, World, project_rec, replay_of, task, tasks_body, token_count

HOME_BUDGET = 300
PAGE = {}  # Zoho serves page 1 of 100 by default, so the plain call carries no paging params


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def sample_tasks():
    return tasks_body(
        task(1, "Prepare release notes", "Open", "Alex Sample", end="2026-10-15"),               # overdue
        task(2, "Fix checkout validation", "In Progress", "Bea Fixture", end="2026-10-23T00:00:00.000Z"),  # soon
        task(3, "Plan next quarter", "Open", "Alex Sample", end="2026-12-01"),                   # far away
        task(4, "Archive old assets", "Closed", "Alex Sample", closed=True, end="2026-10-10"),   # closed: ignored
        task(5, "Triage inbox", "In Progress", "Bea Fixture"),                                   # no date
        task(6, "Ship hotfix", "Open", "Chidi Anagonye", end="2026-10-28"),                      # soon (last day)
    )


def live_replay():
    return replay_of((PR, project_rec()), (f"{PR}/tasks", sample_tasks(), PAGE))


def test_home_shows_the_project_open_counts_by_status_and_what_is_due(world):
    code, out, err = world.run([], live_replay())
    assert code == 0
    assert out == "\n".join([
        'project: "2200"',
        "name: Demo Website Redesign",
        "status: In Progress",
        "counts: open_tasks=5 overdue=1 due_soon=2 open=3 in_progress=2",
        "due[3]{key,title,due,owner}:",
        "  DM1-T1,Prepare release notes,2026-10-15,Alex Sample",
        "  DM1-T2,Fix checkout validation,2026-10-23,Bea Fixture",
        "  DM1-T6,Ship hotfix,2026-10-28,Chidi Anagonye",
        "help[3]{run}:",
        "  zoho-projects-axi task DM1-T1",
        "  zoho-projects-axi tasks",
        "  zoho-projects-axi project",
    ])
    assert world.requests(err) == 2  # cold: the project record and one page of tasks


def test_home_by_name_equals_the_bare_invocation(world):
    bare = world.run([], live_replay())
    named = world.run(["home"])
    assert named[:2] == bare[:2]


def test_warm_home_makes_zero_requests(world):
    cold = world.run([], live_replay())
    warm = world.run([])
    assert warm[:2] == cold[:2] and world.requests(warm[2]) == 0


def test_nothing_due_is_an_explicit_empty_state(world):
    body = tasks_body(task(1, "Someday", "Open", end="2027-05-01"))
    _, out, _ = world.run([], replay_of((PR, project_rec()), (f"{PR}/tasks", body, PAGE)))
    assert "counts: open_tasks=1 overdue=0 due_soon=0 open=1" in out
    assert "due: 0 results" in out
    assert "zoho-projects-axi task " not in out


def test_more_than_one_page_of_tasks_says_the_counts_are_partial(world):
    body = tasks_body(task(1, "A", end="2026-10-22"), has_next=True)
    _, out, _ = world.run([], replay_of((PR, project_rec()), (f"{PR}/tasks", body, PAGE)))
    assert "counts: open_tasks=1+ overdue=0 due_soon=1 open=1" in out
    assert "counts cover the first 1 tasks only" in out


def test_a_failing_task_fetch_is_a_warning_and_home_still_shows_the_project(world):
    code, out, _ = world.run([], replay_of((PR, project_rec()), (f"{PR}/tasks", {}, PAGE, 500)))
    assert code == 0
    assert "name: Demo Website Redesign" in out and "tasks unavailable" in out


def test_the_project_flag_overrides_the_default(world):
    replay = replay_of((f"{P}/projects/777", project_rec("777", "Other")), (f"{P}/projects/777/tasks", tasks_body(), PAGE))
    code, out, _ = world.run(["--project", "777"], replay)
    assert code == 0 and 'project: "777"' in out and "name: Other" in out


def test_without_a_default_project_it_lists_projects_and_hints_project_use(tmp_path):
    w = World(tmp_path, configured=False, ZOHO_PROJECTS_AXI_PORTAL_ID="1100")
    code, out, _ = w.run([], replay_of((f"{P}/projects", [project_rec(), project_rec("2201", "Mobile App")])))
    assert code == 0
    assert "warnings[1]{message}:" in out and "no default project is set" in out
    assert "projects[2]{id,name,status,portal}:" in out
    assert "zoho-projects-axi project use 'Demo Website Redesign'" in out


def test_without_credentials_home_stays_offline_and_points_at_login_help(tmp_path):
    w = World(tmp_path, configured=False)
    w.env = {k: v for k, v in w.env.items() if "SECRET" not in v}
    code, out, err = w.run([])
    assert code == 0 and w.requests(err) == 0
    assert 'credentials: "missing: client_id, client_secret, refresh_token"' in out
    assert "zoho-projects-axi auth login-help" in out


def test_a_missing_project_record_is_not_found_exit_4(world):
    code, out, _ = world.run([], replay_of((PR, {}, None, 404)))
    assert code == 4 and out.splitlines()[0] == "error: not_found"


def test_home_stays_inside_the_token_budget_with_a_big_project(world):
    rows = [task(n, f"Investigate intermittent failure in the nightly export pipeline step {n}", "In Progress",
                 "Alex Sample", end=f"2026-10-{10 + n % 15:02d}") for n in range(1, 101)]
    _, out, _ = world.run([], replay_of((PR, project_rec()), (f"{PR}/tasks", tasks_body(*rows), PAGE)))
    assert "due[5]" in out
    assert token_count(out) <= HOME_BUDGET, token_count(out)


def test_json_home(world):
    import json

    _, out, _ = world.run(["--json"], live_replay())
    doc = json.loads(out)
    assert doc["counts"]["overdue"] == 1 and [r["key"] for r in doc["due"]] == ["DM1-T1", "DM1-T2", "DM1-T6"]
