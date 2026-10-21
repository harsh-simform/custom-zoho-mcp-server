"""T10 wiring at the product seam: `cache status|clear`, `--fresh`, `--project` on every command, and
the absence of the old stateful selection commands."""
import pytest
from support.world import PR, World, replay_of, task, tasks_body

from zoho_axi.cli.registry import REGISTRY

PAGE = {"page": "1", "per_page": "100"}


def tasks_replay():
    return replay_of((f"{PR}/tasks", tasks_body(task(1, "A")), PAGE))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_cache_status_reports_entries_after_a_warm_up(world):
    world.run(["tasks"], tasks_replay())
    code, out, err = world.run(["cache", "status"])
    assert code == 0 and world.requests(err) == 0
    assert "path: " in out and "entries=1" in out
    assert "zoho-axi cache clear --yes" in out


def test_cache_status_on_a_fresh_machine_does_not_create_the_cache(world):
    code, out, _ = world.run(["cache", "status"])
    assert code == 0 and "size: not created yet" in out
    assert not (world.tmp / ".cache").exists()


def test_cache_clear_without_yes_is_a_usage_error_and_never_prompts(world):
    world.run(["tasks"], tasks_replay())
    code, out, _ = world.run(["cache", "clear"])
    assert code == 2 and out.splitlines()[0] == "error: usage.confirmation_required"
    assert "entries=1" in world.run(["cache", "status"])[1]


def test_cache_clear_yes_empties_the_cache_so_the_next_call_hits_zoho_again(world):
    world.run(["tasks"], tasks_replay())
    code, out, _ = world.run(["cache", "clear", "--yes"])
    assert code == 0 and "cleared: 1" in out
    code, _, err = world.run(["tasks"], tasks_replay())
    assert code == 0 and world.requests(err) == 1


def test_fresh_bypasses_the_cache_and_refreshes_it(world):
    world.run(["tasks"], tasks_replay())
    code, _, err = world.run(["tasks", "--fresh"], tasks_replay())
    assert code == 0 and world.requests(err) == 1
    assert world.requests(world.run(["tasks"])[2]) == 0  # and the refreshed entry serves the next call


def test_a_task_key_seen_in_a_list_resolves_to_its_id_with_no_request(world):
    from support.world import PORTAL, PROJECT

    from zoho_axi.core.cache import CacheStore, Index, default_cache_path
    from zoho_axi.core.config import load_settings

    world.run(["tasks"], tasks_replay())
    store = CacheStore(default_cache_path(world.env))
    settings = load_settings(env=world.env, legacy_env_file=world.tmp / "none.env")
    assert Index(store, settings, portal=PORTAL, project=PROJECT).resolve("task", "DM1-T1") == "3001"
    store.close()


@pytest.mark.parametrize("name", ["select_project", "switch_project", "select", "switch", "set_project", "stage"])
def test_the_old_stateful_commands_do_not_exist(name, world):
    code, out, _ = world.run([name])
    assert code == 2 and out.splitlines()[0] == "error: usage.unknown_command"


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.name)
def test_every_command_accepts_the_global_project_flag(spec, world):
    code, out, _ = world.run([*spec.name.split(), "--project", "1", "--help"])
    assert code == 0 and out.startswith(f"usage: zoho-axi {spec.name}")
