"""Seam: `CachedGateway.get` + `request_count` in front of the real gateway, driven by the
fixture-replay transport; plus the derived index's `resolve` and the cache commands' Results.

Time is a fake clock injected into the store (wall-clock seconds, because entries outlive the process).
Zoho bodies are invented-but-plausible shapes (recorded fixtures arrive with T04). TTL expectations
are the defaults documented in the T07 ticket: long 24h, tasks/bugs 5 min, comments/history 2 min.
"""
import multiprocessing
import sqlite3
import time

import pytest
from support import cache_worker
from support.factories import API, make_settings
from support.replay import ReplayTransport

from zoho_axi.core.cache import CachedGateway, CacheStore, cache_clear, cache_status
from zoho_axi.core.errors import NotFoundError, UsageError
from zoho_axi.core.gateway import ZohoGateway
from zoho_axi.core.render import render

IDS = {"portal": "11", "project": "22"}
TASK_URL = f"{API}/portal/11/projects/22/tasks/33"
TASK_BODY = {"id": "33", "name": "Fix login", "key": "PROJ1-T1000"}


class FakeClock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build(tmp_path, replay, *, clock=None, fresh=False, busy_ms=5000, **env):
    settings = make_settings(tmp_path, **env)
    gateway = ZohoGateway(settings, transport=replay, token_provider=lambda: "tok-abc", sleep=lambda s: None)
    store = CacheStore(tmp_path / "cache" / "cache.sqlite", clock=clock or FakeClock(), busy_timeout_ms=busy_ms)
    return CachedGateway(gateway, store, settings, fresh=fresh)


def test_a_second_identical_get_within_the_ttl_makes_no_request(tmp_path):
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY)  # registered once: a 2nd request would raise
    cached = build(tmp_path, replay)

    first = cached.get("task", task="33", **IDS)
    second = cached.get("task", task="33", **IDS)

    assert first == second == {"id": "33", "name": "Fix login", "key": "PROJ1-T1000"}
    assert cached.request_count == 1


def test_ttl_classes_expire_independently(tmp_path):
    # (resource, ids, url, body, seconds that are still fresh, seconds that are stale); defaults from T07
    cases = [
        ("users", {"portal": "11"}, f"{API}/portal/11/users", [{"id": "5", "name": "Asha K"}], 24 * 3600 - 1, 24 * 3600 + 1),
        ("tasks", IDS, f"{API}/portal/11/projects/22/tasks", [{"id": "1"}], 299, 301),
        ("task_comments", {"task": "33", **IDS}, f"{API}/portal/11/projects/22/tasks/33/comments", [{"id": "9"}], 119, 121),
    ]
    for resource, ids, url, body, fresh_for, stale_after in cases:
        clock = FakeClock()
        replay = ReplayTransport().get(url, json=body).get(url, json=body)
        cached = build(tmp_path / resource, replay, clock=clock)

        cached.get(resource, **ids)
        clock.advance(fresh_for)
        cached.get(resource, **ids)
        assert cached.request_count == 1, f"{resource} should still be fresh after {fresh_for}s"
        clock.advance(stale_after - fresh_for)
        cached.get(resource, **ids)
        assert cached.request_count == 2, f"{resource} should have expired after {stale_after}s"


def test_a_configured_ttl_overrides_the_default(tmp_path):
    clock = FakeClock()
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY, repeat=True)
    cached = build(tmp_path, replay, clock=clock, ZOHO_AXI_TTL_TASK_SECONDS="10")
    cached.get("task", task="33", **IDS)
    clock.advance(11)
    cached.get("task", task="33", **IDS)
    assert cached.request_count == 2


def test_fresh_skips_the_read_but_still_writes(tmp_path):
    clock = FakeClock()
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY, repeat=True)
    build(tmp_path, replay, clock=clock).get("task", task="33", **IDS)

    clock.advance(250)
    fresh = build(tmp_path, replay, clock=clock, fresh=True)
    fresh.get("task", task="33", **IDS)
    assert fresh.request_count == 1  # went to the network despite a warm entry

    clock.advance(100)  # 350s after the first fetch (stale) but only 100s after the --fresh refresh
    later = build(tmp_path, replay, clock=clock)
    later.get("task", task="33", **IDS)
    assert later.request_count == 0  # served from the entry the --fresh call wrote


def test_errors_are_never_cached(tmp_path):
    replay = ReplayTransport().get(TASK_URL, status=404).get(TASK_URL, json=TASK_BODY)
    cached = build(tmp_path, replay)
    with pytest.raises(NotFoundError):
        cached.get("task", task="33", **IDS)
    assert cached.get("task", task="33", **IDS) == {"id": "33", "name": "Fix login", "key": "PROJ1-T1000"}
    assert cached.request_count == 2


def test_different_params_and_ids_are_different_entries(tmp_path):
    replay = ReplayTransport()
    replay.get(f"{API}/portal/11/projects/22/tasks", params={"range": "10"}, json={"tasks": [{"id": 1}]})
    replay.get(f"{API}/portal/11/projects/22/tasks", params={"range": "20"}, json={"tasks": [{"id": 2}]})
    cached = build(tmp_path, replay)

    assert [t["id"] for t in cached.get("tasks", params={"range": "10"}, **IDS)] == ["1"]
    assert [t["id"] for t in cached.get("tasks", params={"range": "20"}, **IDS)] == ["2"]
    assert [t["id"] for t in cached.get("tasks", params={"range": "10"}, **IDS)] == ["1"]
    assert cached.request_count == 2


def test_the_same_ids_in_another_project_do_not_collide(tmp_path):
    replay = ReplayTransport()
    replay.get(f"{API}/portal/11/projects/22/tasks/33", json={"id": "33", "name": "In 22"})
    replay.get(f"{API}/portal/11/projects/44/tasks/33", json={"id": "33", "name": "In 44"})
    cached = build(tmp_path, replay)
    assert cached.get("task", task="33", portal="11", project="22")["name"] == "In 22"
    assert cached.get("task", task="33", portal="11", project="44")["name"] == "In 44"


def test_a_collection_keeps_its_has_more_flag_and_survives_a_new_process(tmp_path):
    clock = FakeClock()
    url = f"{API}/portal/11/projects/22/tasks"
    replay = ReplayTransport().get(url, json={"tasks": [{"id": 1}], "page_info": {"has_next_page": True}})
    build(tmp_path, replay, clock=clock).get("tasks", **IDS)

    reopened = build(tmp_path, ReplayTransport(), clock=clock)  # empty replay: any request would raise
    tasks = reopened.get("tasks", **IDS)
    assert [t["id"] for t in tasks] == ["1"]
    assert tasks.has_more is True
    assert reopened.request_count == 0


def db_bytes(tmp_path) -> bytes:
    folder = tmp_path / "cache"
    return b"".join(p.read_bytes() for p in sorted(folder.glob("cache.sqlite*")))


def test_cache_files_are_private_and_hold_no_secrets(tmp_path):
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY)
    cached = build(tmp_path, replay)
    cached.get("task", task="33", **IDS)

    files = sorted((tmp_path / "cache").glob("cache.sqlite*"))
    assert files, "the cache wrote nothing"
    for path in files:
        assert path.stat().st_mode & 0o777 == 0o600, path.name
    assert (tmp_path / "cache").stat().st_mode & 0o777 == 0o700
    data = db_bytes(tmp_path)
    assert b"Fix login" in data  # sanity: we are scanning the right bytes
    for secret in (b"super-secret-value", b"refresh-token-value", b"tok-abc"):
        assert secret not in data


def test_a_response_that_echoes_a_configured_secret_is_not_stored(tmp_path):
    leaky = {"id": "33", "name": "debug: refresh-token-value"}
    replay = ReplayTransport().get(TASK_URL, json=leaky, repeat=True)
    cached = build(tmp_path, replay)

    assert cached.get("task", task="33", **IDS) == leaky  # the caller still gets its answer
    cached.get("task", task="33", **IDS)
    assert cached.request_count == 2  # but it was never cached
    assert b"refresh-token-value" not in db_bytes(tmp_path)


def test_a_corrupt_database_degrades_to_no_cache_with_a_warning(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "cache.sqlite").write_bytes(b"this is not a sqlite file" * 50)
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY, repeat=True)
    cached = build(tmp_path, replay)

    assert cached.get("task", task="33", **IDS)["name"] == "Fix login"
    assert cached.get("task", task="33", **IDS)["name"] == "Fix login"
    assert cached.request_count == 2  # no cache, but no failure either
    assert len(cached.warnings) == 1 and "cache" in cached.warnings[0]


def test_a_locked_database_still_answers_and_warns(tmp_path):
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY, repeat=True)
    build(tmp_path, replay).get("task", task="33", **IDS)  # creates the schema

    blocker = sqlite3.connect(tmp_path / "cache" / "cache.sqlite", isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # holds the write lock like another busy process
    try:
        cached = build(tmp_path, replay, fresh=True, busy_ms=50)
        assert cached.get("task", task="33", **IDS)["name"] == "Fix login"  # fresh: read skipped, write blocked
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert len(cached.warnings) == 1 and "locked" in cached.warnings[0]


# --- derived index: names -> ids with zero API calls once warm ---------------------------------

USERS_URL = f"{API}/portal/11/users"
USERS = [{"id": 5, "name": "Asha K"}, {"id": 6, "name": "Ravi Patel"}, {"id": 7, "name": "Ravi Shah"}]


def warm_users(tmp_path, clock=None):
    replay = ReplayTransport().get(USERS_URL, json={"users": USERS}, repeat=True)
    cached = build(tmp_path, replay, clock=clock)
    cached.get("users", portal="11")
    return cached


def test_a_warm_index_resolves_names_and_ids_without_any_request(tmp_path):
    cached = warm_users(tmp_path)
    before = cached.request_count
    index = cached.index(portal="11", project="22")

    assert index.resolve("user", "Asha K") == "5"
    assert index.resolve("user", "  asha   k ") == "5"  # case and whitespace do not matter
    assert index.resolve("user", "6") == "6"  # an id resolves to itself
    assert index.resolve("user", "Asha") == "5"  # a unique prefix is enough
    assert index.name_of("user", "7") == "Ravi Shah"
    assert cached.request_count == before


def test_unknown_or_ambiguous_names_resolve_to_none(tmp_path):
    index = warm_users(tmp_path).index(portal="11", project="22")
    assert index.resolve("user", "Nobody") is None
    assert index.resolve("user", "Ravi") is None  # matches two users
    assert index.resolve("tag", "bug") is None  # nothing cached for tags yet


def test_a_cold_index_answers_none_and_never_calls_the_api(tmp_path):
    cached = build(tmp_path, ReplayTransport())  # any request would raise
    assert cached.index(portal="11", project="22").resolve("user", "Asha K") is None
    assert cached.request_count == 0


def test_tasklists_milestones_and_tags_are_indexed_and_scoped_to_their_project(tmp_path):
    replay = ReplayTransport()
    replay.get(f"{API}/portal/11/tags", json={"tags": [{"id": 1, "name": "urgent"}]})
    replay.get(f"{API}/portal/11/projects/22/tasklists", json={"tasklists": [{"id": 8, "name": "Auth"}]})
    replay.get(f"{API}/portal/11/projects/22/milestones", json={"milestones": [{"id": 3, "name": "Beta"}]})
    cached = build(tmp_path, replay)
    cached.get("tags", portal="11")
    cached.get("tasklists", **IDS)
    cached.get("milestones", **IDS)

    mine = cached.index(portal="11", project="22")
    assert (mine.resolve("tag", "Urgent"), mine.resolve("tasklist", "auth"), mine.resolve("milestone", "Beta")) == ("1", "8", "3")
    other = cached.index(portal="11", project="44")
    assert other.resolve("tasklist", "Auth") is None  # another project's lists are not ours
    assert other.resolve("tag", "urgent") == "1"  # tags are portal-wide


def test_the_index_goes_stale_with_the_long_ttl_and_a_refetch_replaces_it(tmp_path):
    clock = FakeClock()
    replay = ReplayTransport()
    replay.get(USERS_URL, json={"users": USERS})
    replay.get(USERS_URL, json={"users": [{"id": 5, "name": "Asha K"}]})  # Ravi left the portal
    cached = build(tmp_path, replay, clock=clock)
    cached.get("users", portal="11")
    index = cached.index(portal="11", project="22")

    clock.advance(24 * 3600 + 1)
    assert index.resolve("user", "Asha K") is None  # too old to trust
    cached.get("users", portal="11")  # entry also expired, so this refetches and re-indexes
    assert index.resolve("user", "Asha K") == "5"
    assert index.resolve("user", "Ravi Patel") is None


def test_the_index_is_filled_even_by_a_fresh_run_and_survives_a_new_process(tmp_path):
    clock = FakeClock()
    replay = ReplayTransport().get(USERS_URL, json={"users": USERS})
    build(tmp_path, replay, clock=clock, fresh=True).get("users", portal="11")

    reopened = build(tmp_path, ReplayTransport(), clock=clock)
    assert reopened.index(portal="11", project="22").resolve("user", "Ravi Shah") == "7"


def test_task_and_bug_keys_are_remembered_as_ids(tmp_path):
    replay = ReplayTransport()
    replay.get(TASK_URL, json=TASK_BODY)
    replay.get(f"{API}/portal/11/projects/22/bugs/9", json={"id": "9", "key": "PROJ1-I9", "title": "Crash"})
    replay.get(f"{API}/portal/11/projects/22/tasks", json={"tasks": [{"id": 40, "key": "PROJ1-T1040"}]})
    cached = build(tmp_path, replay)
    index = cached.index(portal="11", project="22")
    assert index.resolve("task", "PROJ1-T1000") is None  # nothing seen yet

    cached.get("task", task="33", **IDS)
    cached.get("bug", bug="9", **IDS)
    cached.get("tasks", **IDS)

    assert index.resolve("task", "PROJ1-T1000") == "33"
    assert index.resolve("task", "proj1-t1000") == "33"  # keys are case-insensitive
    assert index.resolve("bug", "PROJ1-I9") == "9"
    assert index.resolve("task", "PROJ1-T1040") == "40"
    assert index.resolve("bug", "PROJ1-T1000") is None  # a task key is not a bug
    assert index.resolve_key("PROJ1-I9", kind="bug") == "9"
    index.remember_key("PROJ1-T2000", "77", kind="task")  # the Search-API path of T08 records its finds
    assert index.resolve("task", "PROJ1-T2000") == "77"


def _v3_page(n):
    # invented-but-plausible v3 list rows: the human key is `prefix`, there is no `key`
    return {"tasks": [{"id": str(3000 + i), "prefix": f"DM1-T{i}", "name": f"Task {i}"} for i in range(1, n + 1)]}


def test_v3_prefix_keys_are_learned_from_a_task_page(tmp_path):
    replay = ReplayTransport().get(f"{API}/portal/11/projects/22/tasks", json=_v3_page(3))
    cached = build(tmp_path, replay)
    cached.get("tasks", **IDS)
    index = cached.index(portal="11", project="22")
    assert index.resolve("task", "DM1-T2") == "3002"
    assert index.known_keys() == [("task", "DM1-T1"), ("task", "DM1-T2"), ("task", "DM1-T3")]


def test_a_100_row_page_writes_its_keys_in_one_transaction(tmp_path, monkeypatch):
    log: list[tuple[bool, str]] = []  # (inside an explicit transaction, statement)
    state = {"open": False}
    real_connect = sqlite3.connect

    def tracing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)

        def trace(statement):
            head = statement.strip().upper()
            if head.startswith("BEGIN"):
                state["open"] = True
            log.append((state["open"], head))
            if head.startswith(("COMMIT", "ROLLBACK", "END")):
                state["open"] = False

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracing_connect)
    replay = ReplayTransport().get(f"{API}/portal/11/projects/22/tasks", json=_v3_page(100))
    cached = build(tmp_path, replay)
    cached.get("tasks", **IDS)

    key_inserts = [inside for inside, stmt in log if "INTO TASK_KEYS" in stmt]
    assert len(key_inserts) == 100 and all(key_inserts), "every key insert must run inside an open transaction"
    assert [stmt for _, stmt in log].count("BEGIN IMMEDIATE") <= 2  # the key batch (+ at most one index batch)
    assert cached.index(portal="11", project="22").resolve("task", "DM1-T100") == "3100"


def test_two_processes_hammering_one_cache_do_not_corrupt_or_warn(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    workers = [ctx.Process(target=cache_worker.run, args=(str(tmp_path), fresh, start)) for fresh in (True, False)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(60)
    assert [w.exitcode for w in workers] == [0, 0]  # 3 would mean a degraded-cache warning

    after = build(tmp_path, ReplayTransport(), clock=time.time)  # empty replay: every read must be a hit
    for n in range(cache_worker.TASKS):
        assert after.get("task", task=str(n), **IDS)["id"] == str(n)
    assert after.index(portal="11").resolve("user", "Asha K") == "5"
    assert after.index(portal="11", project="22").resolve("task", "P-T7") == "7"
    assert after.request_count == 0 and not after.warnings


# --- `zoho-axi cache status|clear` as plain functions returning a Result --------------------------

def test_status_reports_entries_and_the_oldest_one(tmp_path):
    clock = FakeClock()  # 1_800_000_000 == 2027-01-15T08:00:00Z
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY).get(USERS_URL, json={"users": USERS})
    cached = build(tmp_path, replay, clock=clock)
    cached.get("task", task="33", **IDS)
    clock.advance(60)
    cached.get("users", portal="11")

    result = cache_status(tmp_path / "cache" / "cache.sqlite", clock=clock)

    assert result.entity == "cache"
    assert result.payload["path"] == str(tmp_path / "cache" / "cache.sqlite")
    assert result.payload["oldest"] == "2027-01-15T08:00:00Z"
    assert result.counts["entries"] == 2
    assert result.counts["users"] == 3
    text = render(result).text
    assert "entries=2" in text and "oldest: " in text and "zoho-axi cache clear --yes" in text


@pytest.mark.parametrize("odd", ["what?now", "a#b", "100%25 sure", "sp ace&amp=1", "q?mode=rw#x%3f"])
def test_status_reads_a_cache_whose_path_has_uri_special_characters(tmp_path, odd):
    base = tmp_path / odd
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY)
    build(base, replay).get("task", task="33", **IDS)

    result = cache_status(base / "cache" / "cache.sqlite")

    assert result.counts["entries"] == 1  # not "cache unreadable", not a different/empty database
    assert result.warnings == []


def test_status_of_a_missing_cache_says_so_and_creates_nothing(tmp_path):
    path = tmp_path / "nowhere" / "cache.sqlite"
    result = cache_status(path)
    assert result.counts["entries"] == 0
    assert not path.exists() and not path.parent.exists()


def test_clear_needs_yes_and_never_prompts(tmp_path):
    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY)
    cached = build(tmp_path, replay)
    cached.get("task", task="33", **IDS)
    path = tmp_path / "cache" / "cache.sqlite"

    with pytest.raises(UsageError) as refusal:
        cache_clear(False, path)
    assert refusal.value.exit_code == 2 and "--yes" in (refusal.value.hint or "")
    assert path.exists()
    cached.get("task", task="33", **IDS)
    assert cached.request_count == 1  # still warm


def test_clear_with_yes_empties_everything_including_the_index(tmp_path):
    replay = ReplayTransport().get(USERS_URL, json={"users": USERS}, repeat=True)
    cached = build(tmp_path, replay)
    cached.get("users", portal="11")
    path = tmp_path / "cache" / "cache.sqlite"

    result = cache_clear(True, path)

    assert result.entity == "cache" and result.payload["cleared"] == 1
    assert cache_status(path).counts["entries"] == 0
    after = build(tmp_path, replay)
    assert after.index(portal="11").resolve("user", "Asha K") is None
    after.get("users", portal="11")
    assert after.request_count == 1  # cold again, and the cache works again after a clear


def test_clear_repairs_a_corrupt_cache(tmp_path):
    path = tmp_path / "cache" / "cache.sqlite"
    path.parent.mkdir()
    path.write_bytes(b"garbage" * 100)
    status = cache_status(path)
    assert status.warnings  # unreadable, reported rather than raised
    cache_clear(True, path)

    replay = ReplayTransport().get(TASK_URL, json=TASK_BODY)
    cached = build(tmp_path, replay)
    cached.get("task", task="33", **IDS)
    cached.get("task", task="33", **IDS)
    assert cached.request_count == 1 and not cached.warnings
