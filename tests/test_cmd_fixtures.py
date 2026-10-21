"""T10 commands against T04's committed (synthetic, documented-shape) Zoho fixtures: the same product seam
as the other test_cmd_* files, but the bodies are the shared fixture set rather than inline builders, so
the commands are checked against shapes (bare-array projects, wrapped projects, v3.1 users, legacy
milestones, 24 tasks with a next page) that were laid out independently of this ticket. Expected values
are literals read off the fixture files. Token budgets use the shared helper (tests/support/tokens.py).
"""
import pytest
from support.fixtures import PORTAL, PROJECT, first_json, replay_for
from support.tokens import assert_token_budget
from support.world import World

BUDGET_HOME = 300
BUDGET_PROJECT = 400
BUDGET_TASKS_20 = 500


@pytest.fixture
def world(tmp_path):
    return World(tmp_path, configured=False, ZOHO_PROJECTS_AXI_PORTAL_ID=PORTAL, ZOHO_PROJECTS_AXI_PROJECT_ID=PROJECT)


def test_tasks_from_the_fixture_list_cut_at_the_default_limit_with_a_next_page_hint(world):
    code, out, err = world.run(["tasks"], replay_for("tasks/list"))
    assert code == 0 and world.requests(err) == 1
    lines = out.splitlines()
    assert lines[0] == "counts: matched=24 shown=20 open=8 in_progress=8 closed=8"
    assert lines[1] == "tasks[20]{key,title,status,owner}:"
    assert lines[2] == "  DM1-T1,Prepare release notes,Open,Hugo Okoye"
    assert 'truncated[1]: "tasks (+4 rows, --limit 24)"' in out
    assert "zoho-projects-axi tasks --page 2" in out  # the fixture's page_info says has_next_page
    assert "Unassigned" not in out
    assert_token_budget(out, BUDGET_TASKS_20, label="tasks 20 rows (fixture)")


def test_owner_filter_on_the_fixture(world):
    _, out, _ = world.run(["tasks", "--owner", "sana"], replay_for("tasks/list"))
    assert out.splitlines()[0] == "counts: matched=8 closed=8"
    assert "DM1-T3,Update onboarding copy,Closed,Sana Hale" in out


def test_the_small_task_list_fixture_also_renders_as_json(world):
    code, out, _ = world.run(["tasks", "--json"], replay_for("tasks/list_small"))
    assert code == 0 and '"tasks":[{"key":"DM1-T1"' in out


@pytest.mark.parametrize("fixture", ["projects/list", "projects/list_wrapped"])
def test_project_list_accepts_both_the_bare_array_and_the_wrapped_shape(fixture, world):
    code, out, _ = world.run(["project", "list"], replay_for(fixture))
    assert code == 0
    assert out.splitlines()[:2] == ["counts: projects=2", "projects[2]{id,name,status,portal}:"]
    assert '  "20000000001",Demo Website Redesign,In Progress,"10000000001"' in out


def test_project_list_without_a_portal_starts_from_the_portals_fixture(tmp_path):
    w = World(tmp_path, configured=False)
    code, out, err = w.run(["project", "list"], replay_for("portals/list", "projects/list"))
    assert code == 0 and w.requests(err) == 2 and "projects[2]" in out


def test_project_overview_from_fixtures_with_the_milestones_fallback(world):
    replay = replay_for("projects/detail", "tasklists/list", "milestones/v3_404_legacy_ok", "users/list", "tags/list")
    code, out, err = world.run(["project"], replay)
    assert code == 0 and world.requests(err) == 6  # the milestones v3 404 and its legacy retry both count
    assert out.splitlines()[:7] == [
        'project: "20000000001"',
        "name: Demo Website Redesign",
        "status: Active",
        "start: 2026-01-05",
        "end: 2026-09-30",
        "next_milestone: Beta Release (2026-04-30)",
        "counts: tasklists=2 milestones=2 users=2 tags=2 open_tasks=15 closed_tasks=10",
    ]
    assert "warnings" not in out
    assert_token_budget(out, BUDGET_PROJECT, label="project overview (fixtures)")


def test_home_from_fixtures(world):
    code, out, err = world.run([], replay_for("projects/detail", "tasks/list"))
    assert code == 0 and world.requests(err) == 2
    assert out.splitlines()[:5] == [
        'project: "20000000001"',
        "name: Demo Website Redesign",
        "status: Active",
        # the fixture tasks carry no due dates, and its page_info says more pages exist (hence the `+`)
        "counts: open_tasks=16+ overdue=0 due_soon=0 open=8 in_progress=8",
        "due: 0 results",
    ]
    assert_token_budget(out, BUDGET_HOME, label="home (fixtures)")


@pytest.mark.parametrize("users,first", [
    ("users/list", '  "10000000001",Hugo Okoye,Administrator'),  # display_name is only "Hugo": full_name wins
    ("users/list_v3_1_shape", '  "10000000001",Hugo Okoye,Administrator'),
])
def test_people_accepts_both_user_shapes(users, first, world):
    code, out, _ = world.run(["people"], replay_for(users, "tags/list"))
    assert code == 0
    assert first in out.splitlines()
    assert '  "1551200003123",checkout' in out.splitlines()


def test_tasklist_name_resolves_against_the_tasklists_fixture(world):
    tasklist = first_json("tasklists/list")["tasklists"][0]
    flt = ('{"criteria": [{"field_name": "tasklist_id", "criteria_condition": "is", "value": ["%s"]}], "pattern": "1"}'
           % tasklist["id"])
    replay = replay_for("tasklists/list")
    replay.add("GET", f"https://projectsapi.zoho.com/api/v3/portal/{PORTAL}/projects/{PROJECT}/tasks",
               params={"filter": flt}, json=first_json("tasks/list_small"))
    code, out, err = world.run(["tasks", "--tasklist", tasklist["name"].lower()], replay)
    assert code == 0 and world.requests(err) == 2 and "tasks[" in out
