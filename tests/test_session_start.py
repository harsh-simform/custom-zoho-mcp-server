"""T14: `zoho-axi session-start`, the Claude Code SessionStart hook entrypoint, at the product seam.

Contract (ticket 14): <= 150 tokens; project, short status tally, "run `zoho-axi` for live data" and the top
3 command forms; reads the cache, and makes a network call only when the cache is stale and credentials
exist; and in every unconfigured / offline / failing case prints nothing, writes nothing to stderr and
exits 0. Bodies are the invented-but-plausible builders of tests/support/world.py. The replay transport
records every request, so "no network" is `replay.calls == []`.
"""
import io
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from support.golden import assert_golden
from support.tokens import assert_token_budget
from support.replay import ReplayTransport
from support.world import CREDS, PR, TODAY, World, project_rec, replay_of, task, tasks_body

from zoho_axi.cli import main as cli_main
from zoho_axi.cli.main import main
from zoho_axi.core.cache import default_cache_path
from zoho_axi.core.config import load_settings
from zoho_axi.core.gateway import ZohoGateway
from zoho_axi.core.transport import TransportError

HOOK_BUDGET_TOKENS = 150
PAGE = {}
HOUR = 3600


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def sample_tasks():
    return tasks_body(
        task(1, "Prepare release notes", "Open", "Alex Sample", end="2026-10-15"),
        task(2, "Fix checkout validation", "In Progress", "Bea Fixture", end="2026-10-23T00:00:00.000Z"),
        task(3, "Plan next quarter", "Open", "Alex Sample", end="2026-12-01"),
        task(4, "Archive old assets", "Closed", "Alex Sample", closed=True, end="2026-10-10"),
        task(5, "Triage inbox", "In Progress", "Bea Fixture"),
        task(6, "Ship hotfix", "Open", "Chidi Anagonye", end="2026-10-28"),
    )


def live_replay():
    return replay_of((PR, project_rec()), (f"{PR}/tasks", sample_tasks(), PAGE))


def gateway_for(world, replay, env):
    settings = load_settings(env=env, legacy_env_file=world.tmp / "none.env")
    return ZohoGateway(settings, transport=replay, token_provider=lambda: "tok", sleep=lambda s: None)


def hook(world, replay=None, env=None):
    """(exit code, stdout, stderr) of the hook; plain `session-start` as Claude Code runs it."""
    out, err = io.StringIO(), io.StringIO()
    merged = {**world.env, **(env or {})}
    replay = replay if replay is not None else ReplayTransport()
    world.last_replay = replay
    code = main(["session-start"], merged, out, err, gateway=gateway_for(world, replay, merged), today=TODAY)
    return code, out.getvalue(), err.getvalue()


def age_cache(world, seconds):
    conn = sqlite3.connect(default_cache_path(world.env))
    conn.execute("UPDATE entries SET fetched_at = fetched_at - ?", (seconds,))
    conn.commit()
    conn.close()


def warm(world):
    code, _, _ = world.run([], live_replay())
    assert code == 0


# --- what it says --------------------------------------------------------------------------------

def test_warm_cache_gives_the_snapshot_with_no_request(world):
    warm(world)
    code, out, err = hook(world)
    assert code == 0 and err == ""
    assert world.last_replay.calls == []
    assert out == "\n".join([
        'project: "2200"',
        "name: Demo Website Redesign",
        "status: In Progress",
        "note: cached snapshot; run `zoho-axi` for live data",
        "counts: open_tasks=5 overdue=1 due_soon=2 open=3 in_progress=2",
        "help[3]{run}:",
        "  zoho-axi resolve '<text>'",
        "  zoho-axi task DM1-T1",
        "  zoho-axi tasks --status open",
    ]) + "\n"


def test_hook_text_golden(world):
    warm(world)
    assert_golden("hook/session-start", hook(world)[1])


def test_stays_inside_the_token_budget_with_a_big_project(world):
    rows = [task(n, f"Investigate intermittent failure in the nightly export pipeline step {n}",
                 ("Open", "In Progress", "In Review", "Blocked by vendor", "Waiting for customer", "QA")[n % 6],
                 "Alex Sample", end=f"2026-10-{10 + n % 15:02d}") for n in range(1, 101)]
    big = replay_of((PR, project_rec(name="Customer Portal Modernisation Programme 2026")),
                    (f"{PR}/tasks", tasks_body(*rows), PAGE))
    world.run([], big)
    out = hook(world)[1]
    assert out
    assert_token_budget(out, HOOK_BUDGET_TOKENS, label="hook session-start")


# --- cache first, network only when stale ----------------------------------------------------------

def test_a_cache_older_than_the_normal_task_ttl_is_still_used_without_network(world):
    warm(world)
    age_cache(world, 2 * HOUR)  # far past the 5 minute task TTL `home` honours, but not "stale" for a hook
    code, out, _ = hook(world)
    assert code == 0 and "name: Demo Website Redesign" in out
    assert world.last_replay.calls == []


def test_a_stale_cache_with_credentials_refreshes_from_zoho(world):
    warm(world)
    age_cache(world, 3 * 24 * HOUR)
    code, out, err = hook(world, live_replay())
    assert code == 0 and err == ""
    assert len(world.last_replay.calls) == 2  # the project record and one page of tasks
    assert "counts: open_tasks=5" in out


def test_a_cold_cache_with_credentials_fetches_once_then_is_warm(world):
    code, out, _ = hook(world, live_replay())
    assert code == 0 and "name: Demo Website Redesign" in out
    assert len(world.last_replay.calls) == 2
    hook(world)
    assert world.last_replay.calls == []


# --- silence ---------------------------------------------------------------------------------------

def assert_silent(result):
    code, out, err = result
    assert (code, out, err) == (0, "", "")


def test_unconfigured_is_silent(tmp_path):
    w = World(tmp_path, configured=False)
    w.env = {k: v for k, v in w.env.items() if k not in CREDS}
    assert_silent(hook(w))
    assert w.last_replay.calls == []


def test_credentials_without_a_default_project_is_silent(tmp_path):
    w = World(tmp_path, configured=False)
    assert_silent(hook(w))
    assert w.last_replay.calls == []


def test_offline_with_a_cold_cache_is_silent(world):
    down = ReplayTransport()
    down.get(PR, error=TransportError("ConnectError"), repeat=True)
    assert_silent(hook(world, down))


def test_offline_with_a_stale_cache_is_silent(world):
    warm(world)
    age_cache(world, 3 * 24 * HOUR)
    down = ReplayTransport()
    down.get(PR, error=TransportError("ConnectError"), repeat=True)
    down.get(f"{PR}/tasks", error=TransportError("ConnectError"), repeat=True)
    assert_silent(hook(world, down))


def test_a_zoho_error_is_silent(world):
    assert_silent(hook(world, replay_of((PR, {}, None, 500))))


def test_a_failing_task_fetch_is_silent_rather_than_a_half_snapshot(world):
    assert_silent(hook(world, replay_of((PR, project_rec()), (f"{PR}/tasks", {}, PAGE, 500))))


def test_a_corrupt_cache_file_is_silent_and_quiet_on_stderr(world, capsys):
    path = default_cache_path(world.env)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a sqlite database" * 50)
    assert_silent(hook(world))
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_a_hung_network_call_is_abandoned_at_the_deadline_and_stays_silent(world, monkeypatch):
    class Hung:
        request_count = 0

        def get(self, *args, **kwargs):
            time.sleep(3)

    monkeypatch.setattr(cli_main, "HOOK_DEADLINE_SECONDS", 0.3)
    out, err = io.StringIO(), io.StringIO()
    started = time.monotonic()
    code = main(["session-start"], world.env, out, err, gateway=Hung())
    assert (code, out.getvalue(), err.getvalue()) == (0, "", "")
    assert time.monotonic() - started < 2


def test_extra_arguments_from_the_harness_do_not_break_it(world):
    warm(world)
    out, err = io.StringIO(), io.StringIO()
    gateway = gateway_for(world, ReplayTransport(), world.env)
    code = main(["session-start", "--bogus", "--project"], world.env, out, err, gateway=gateway)
    assert code == 0 and err.getvalue() == ""


def test_help_still_works(world):
    code, out, _ = world.run(["session-start", "--help"])
    assert code == 0 and out.startswith("usage: zoho-axi session-start")


def test_as_a_real_process_offline_with_a_corrupt_cache_prints_nothing_at_all(tmp_path):
    """stdout, stderr and the exit code of the installed command. The proxy points at a closed local port,
    so the (stale) refresh fails at once without any traffic leaving the machine."""
    cache = tmp_path / ".cache" / "zoho-axi" / "cache.sqlite"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"corrupt" * 100)
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env"),
           "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": "",
           "ZOHO_AXI_PORTAL_ID": "1100", "ZOHO_AXI_PROJECT_ID": "2200", **CREDS}
    exe = Path(sys.executable).parent / "zoho-axi"
    done = subprocess.run(["/bin/sh", "-c", 'exec "$0" "$@" <&-', str(exe), "session-start"], env=env,
                          capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
