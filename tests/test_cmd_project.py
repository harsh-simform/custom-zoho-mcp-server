"""`zoho-axi project`, `project list`, `project use` (T10) at the product seam. Bodies are
invented-but-plausible (tests/support/world.py); expected texts are hand-written literals."""
import json
import os
import stat

import pytest
from support.factories import API
from support.world import (
    LEGACY_MILESTONES_URL, P, PORTAL, PR, World, legacy_milestones, project_rec, replay_of, tags_body,
    tasklists_body, tasks_body, task, token_count, users_body,
)

PROJECT_BUDGET = 400
MILESTONES = legacy_milestones(
    ("5001", "Beta Release", "04-30-2026 11:59 PM", False),
    ("5002", "Kickoff", "01-31-2026 11:59 PM", True),
    ("5003", "GA", "09-15-2026 11:59 PM", False),
)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def overview_replay(milestones=MILESTONES, project=None):
    return replay_of(
        (PR, project or project_rec()),
        (f"{PR}/tasklists", tasklists_body(("9001", "Auth"), ("9002", "Billing"))),
        (f"{PR}/milestones", {}, None, 404),
        (LEGACY_MILESTONES_URL, milestones),
        (f"{P}/users", users_body(("5001", "Alex Sample", "Admin"), ("5002", "Bea Fixture", "Employee"))),
        (f"{P}/tags", tags_body(("7001", "checkout"), ("7002", "needs-design"))),
    )


# --- project (overview) -------------------------------------------------------------------------

def test_overview_is_a_lean_summary_with_counts_and_the_next_milestone_due(world):
    code, out, err = world.run(["project"], overview_replay())
    assert code == 0
    assert out == "\n".join([
        'project: "2200"',
        "name: Demo Website Redesign",
        "status: In Progress",
        "owner: Alex Sample",
        "start: 2026-01-05",
        "end: 2026-09-30",
        'next_milestone: Beta Release (2026-04-30)',
        "counts: tasklists=2 milestones=3 users=2 tags=2 open_tasks=15 closed_tasks=10",
        "help[3]{run}:",
        "  zoho-axi tasks",
        "  zoho-axi people",
        "  zoho-axi project --lists",
    ])
    assert world.requests(err) == 6  # project, tasklists, milestones v3 404 + legacy, users, tags


def test_overview_warm_makes_zero_requests(world):
    cold = world.run(["project"], overview_replay())
    warm = world.run(["project"])
    assert warm[:2] == cold[:2] and world.requests(warm[2]) == 0


def test_a_failed_milestones_fetch_is_a_warning_not_a_crash(world):
    replay = replay_of(
        (PR, project_rec()),
        (f"{PR}/tasklists", tasklists_body(("9001", "Auth"))),
        (f"{PR}/milestones", {}, None, 404),
        (LEGACY_MILESTONES_URL, {}, None, 404),
        (f"{P}/users", users_body(("5001", "Alex Sample", "Admin"))),
        (f"{P}/tags", tags_body()),
    )
    code, out, _ = world.run(["project"], replay)
    assert code == 0
    assert "milestones=" not in out and "next_milestone" not in out
    assert "counts: tasklists=1 users=1 tags=0 open_tasks=15 closed_tasks=10" in out
    assert "warnings[1]{message}:" in out
    assert "milestones unavailable" in out


def test_a_forbidden_sub_fetch_is_a_warning_but_a_rejected_token_still_fails(world):
    forbidden = replay_of(
        (PR, project_rec()), (f"{PR}/tasklists", tasklists_body()), (f"{PR}/milestones", MILESTONES),
        (f"{P}/users", users_body()), (f"{P}/tags", {}, None, 403))
    code, out, _ = world.run(["project"], forbidden)
    assert code == 0 and "tags unavailable" in out

    world2 = World(world.tmp / "second")
    code, out, _ = world2.run(["project"], replay_of((PR, {}, None, 401)))
    assert code == 3 and out.splitlines()[0] == "error: auth.unauthorized"


def test_missing_project_is_not_found_exit_4(world):
    code, out, _ = world.run(["project"], replay_of((PR, {}, None, 404)))
    assert code == 4 and out.splitlines()[0] == "error: not_found"


def test_lists_flag_adds_tasklists_and_milestones_with_ids_for_filtering(world):
    code, out, _ = world.run(["project", "--lists"], overview_replay())
    assert code == 0
    assert "tasklists[2]{id,name,status}:" in out
    assert '  "9001",Auth,' in out
    assert "milestones[3]{id,name,due,status}:" in out
    assert '  "5001",Beta Release,2026-04-30,notcompleted' in out
    assert "zoho-axi project --lists" not in out


def test_description_only_with_full_and_html_stripped(world):
    _, plain, _ = world.run(["project"], overview_replay())
    assert "Redesign the demo site" not in plain
    _, full, _ = world.run(["project", "--full"])
    assert "description: Redesign the demo site." in full


def test_project_flag_accepts_a_name_through_the_project_list(world):
    other = project_rec("777", "Mobile App")
    replay = replay_of((f"{P}/projects", [project_rec(), other]), (f"{P}/projects/777", other),
                       (f"{P}/projects/777/tasks", tasks_body(task(1, "A")), {"page": "1", "per_page": "100"}))
    code, out, _ = world.run(["tasks", "--project", "mobile"], replay)
    assert code == 0 and "DM1-T1" in out


def test_overview_stays_inside_the_token_budget_with_a_big_project(world):
    many = overview_replay(project=project_rec(description="<p>" + "word " * 2000 + "</p>"))
    _, out, _ = world.run(["project"], many)
    assert token_count(out) <= PROJECT_BUDGET, token_count(out)


# --- project list -------------------------------------------------------------------------------

def test_project_list_shows_the_configured_portals_projects(world):
    body = [project_rec(), project_rec("2201", "Mobile App", status="Closed")]
    code, out, err = world.run(["project", "list"], replay_of((f"{P}/projects", body)))
    assert code == 0
    assert out == "\n".join([
        "counts: projects=2",
        "projects[2]{id,name,status,portal}:",
        '  "2200",Demo Website Redesign,In Progress,"1100"',
        '  "2201",Mobile App,Closed,"1100"',
        "help[1]{run}:",
        "  zoho-axi project use 'Demo Website Redesign'",
    ])
    assert world.requests(err) == 1


def test_project_list_without_a_portal_walks_every_portal(tmp_path):
    w = World(tmp_path, configured=False)
    replay = replay_of(
        (f"{API}/portals", [{"id": "1100", "portal_name": "a"}, {"id": "1101", "portal_name": "b"}]),
        (f"{API}/portal/1100/projects", [project_rec()]),
        (f"{API}/portal/1101/projects", [project_rec("3300", "Other")]),
    )
    code, out, err = w.run(["project", "list"], replay)
    assert code == 0 and "projects[2]" in out
    assert '"3300",Other,In Progress,"1101"' in out
    assert w.requests(err) == 3


def test_project_list_empty_state(world):
    _, out, _ = world.run(["project", "list"], replay_of((f"{P}/projects", [])))
    assert out.splitlines()[:2] == ["counts: projects=0", "projects: 0 results"]


# --- project use --------------------------------------------------------------------------------

CONFIG = """\
# zoho-axi config -- keep this file private
client_id = "cid"            # from the API console
client_secret = 'literal-secret'
dc = "com"

portal_id = "9999"  # old portal
project_id = "1111"  # old project
ttl_task_seconds = 60

[extra]
note = "project_id = nothing to see here"
"""


def write_config(world, text=CONFIG):
    world.config_path.parent.mkdir(parents=True)
    world.config_path.write_text(text)
    os.chmod(world.config_path, 0o644)


def projects_replay():
    return replay_of((f"{API}/portal/9999/projects", [project_rec(), project_rec("2201", "Mobile App")]))


CONFIG_AFTER = CONFIG.replace('project_id = "1111"', 'project_id = "2201"')


def test_project_use_by_name_rewrites_only_the_two_values_and_keeps_everything_else(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    code, out, err = w.run(["project", "use", "mobile"], projects_replay())
    assert code == 0, out
    assert w.config_path.read_text() == CONFIG_AFTER  # comments, quoting styles, other keys and tables intact
    assert out.splitlines()[0] == 'default: "2201"'
    assert "name: Mobile App" in out
    assert w.requests(err) == 1


def test_project_use_changes_the_portal_too_when_given_portal_colon_name(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    replay = replay_of((f"{API}/portal/4444/projects", [project_rec("2201", "Mobile App")]))
    code, _, _ = w.run(["project", "use", "4444:Mobile"], replay)
    assert code == 0
    text = w.config_path.read_text()
    assert 'portal_id = "4444"  # old portal' in text and 'project_id = "2201"  # old project' in text


def test_config_file_is_mode_600_and_no_temp_files_are_left(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    w.run(["project", "use", "2201"], projects_replay())
    assert stat.S_IMODE(w.config_path.stat().st_mode) == 0o600
    assert [p.name for p in w.config_path.parent.iterdir()] == ["config.toml"]


def test_a_missing_config_file_is_created_private_with_just_the_two_keys(tmp_path):
    w = World(tmp_path, configured=False, ZOHO_AXI_PORTAL_ID="1100")
    code, _, _ = w.run(["project", "use", "demo"], replay_of((f"{P}/projects", [project_rec()])))
    assert code == 0
    assert w.config_path.read_text() == 'portal_id = "1100"\nproject_id = "2200"\n'
    assert stat.S_IMODE(w.config_path.stat().st_mode) == 0o600


def test_absent_keys_are_inserted_before_the_first_table(tmp_path):
    w = World(tmp_path, configured=False, ZOHO_AXI_PORTAL_ID="1100")
    write_config(w, '# my config\ndc = "com"\n\n[extra]\nk = 1\n')
    w.run(["project", "use", "demo"], replay_of((f"{P}/projects", [project_rec()])))
    assert w.config_path.read_text() == (
        '# my config\ndc = "com"\nportal_id = "1100"\nproject_id = "2200"\n\n[extra]\nk = 1\n')


def test_the_new_default_is_what_the_next_command_uses(tmp_path):
    w = World(tmp_path, configured=False, ZOHO_AXI_PORTAL_ID="1100")
    w.run(["project", "use", "demo"], replay_of((f"{P}/projects", [project_rec()])))
    page = {"page": "1", "per_page": "100"}
    code, out, _ = w.run(["tasks"], replay_of((f"{P}/projects/2200/tasks", tasks_body(task(1, "A")), page)))
    assert code == 0 and "DM1-T1" in out


def test_project_use_warm_makes_zero_requests(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    w.run(["project", "use", "mobile"], projects_replay())
    code, _, err = w.run(["project", "use", "Mobile App"])
    assert code == 0 and w.requests(err) == 0


def test_an_id_missing_from_the_list_is_validated_with_a_direct_lookup(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    replay = replay_of((f"{API}/portal/9999/projects", [project_rec()]),
                       (f"{API}/portal/9999/projects/5555", project_rec("5555", "Hidden")))
    code, out, _ = w.run(["project", "use", "5555"], replay)
    assert code == 0 and "name: Hidden" in out
    assert 'project_id = "5555"' in w.config_path.read_text()


def test_unknown_project_is_not_found_and_leaves_the_file_alone(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    code, out, _ = w.run(["project", "use", "nonesuch"], projects_replay())
    assert code == 4 and out.splitlines()[0] == "error: not_found"
    assert w.config_path.read_text() == CONFIG


def test_an_unknown_numeric_id_is_not_found(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    replay = replay_of((f"{API}/portal/9999/projects", [project_rec()]), (f"{API}/portal/9999/projects/5", {}, None, 404))
    code, _, _ = w.run(["project", "use", "5"], replay)
    assert code == 4 and w.config_path.read_text() == CONFIG


def test_an_ambiguous_name_is_a_usage_error_listing_candidates(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    replay = replay_of((f"{API}/portal/9999/projects", [project_rec(name="Mobile iOS"), project_rec("2201", "Mobile Android")]))
    code, out, _ = w.run(["project", "use", "mobile"], replay)
    assert code == 2 and "Mobile iOS" in out and "Mobile Android" in out
    assert w.config_path.read_text() == CONFIG


def test_a_failed_write_leaves_the_original_file_untouched(tmp_path, monkeypatch):
    w = World(tmp_path, configured=False)
    write_config(w)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    code, out, _ = w.run(["project", "use", "mobile"], projects_replay())
    assert code == 1 and "error: internal_error" in out
    assert w.config_path.read_text() == CONFIG
    assert [p.name for p in w.config_path.parent.iterdir()] == ["config.toml"]


def test_an_env_override_is_called_out_as_still_winning(tmp_path):
    w = World(tmp_path, configured=False, ZOHO_AXI_PORTAL_ID="9999", ZOHO_AXI_PROJECT_ID="1")
    code, out, _ = w.run(["project", "use", "demo"], replay_of((f"{API}/portal/9999/projects", [project_rec()])))
    assert code == 0
    assert "ZOHO_AXI_PROJECT_ID is set in the environment and still overrides the config file" in out


def test_without_a_portal_and_with_several_it_asks_for_portal_colon_name(tmp_path):
    w = World(tmp_path, configured=False)
    replay = replay_of((f"{API}/portals", [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]))
    code, out, _ = w.run(["project", "use", "demo"], replay)
    assert code == 2 and "PORTAL:demo" in out


def test_without_a_portal_and_with_one_it_uses_that_portal(tmp_path):
    w = World(tmp_path, configured=False)
    replay = replay_of((f"{API}/portals", [{"id": "1100", "name": "a"}]), (f"{P}/projects", [project_rec()]))
    code, _, _ = w.run(["project", "use", "demo"], replay)
    assert code == 0 and 'portal_id = "1100"' in w.config_path.read_text()


def test_project_use_requires_its_argument(world):
    code, out, _ = world.run(["project", "use"])
    assert code == 2 and "required: ref" in out


def test_json_output_of_project_use(tmp_path):
    w = World(tmp_path, configured=False)
    write_config(w)
    _, out, _ = w.run(["project", "use", "mobile", "--json"], projects_replay())
    doc = json.loads(out)
    assert doc["default"] == "2201" and doc["portal"] == "9999"
