"""The local response cache: a transparent layer with the gateway's own `get` / `request_count`
surface, so commands never know whether an answer came from SQLite or from Zoho.

    store = CacheStore(default_cache_path(env))
    gateway = CachedGateway(ZohoGateway(settings), store, settings, fresh=args.fresh)
    gateway.get("task", task="33", portal="11", project="22")   # hits Zoho once, then the cache

Entries are keyed by (portal, project, resource, id, params-hash) and hold the gateway's already
normalized result plus `fetched_at`; the TTL is applied at read time from the settings, so changing a
TTL takes effect immediately. `fresh=True` skips reads but still writes. Errors are never stored, and
neither are downloads (`download` is not even part of this surface) or anything containing a configured
secret. A corrupt or locked database never fails a command: the cache degrades to "no cache" and records
a warning (`CachedGateway.warnings`, also logged to stderr).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import Settings
from .errors import UsageError
from .gateway import ZohoGateway
from .resources import Page
from .result import Hint, Result

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Which TTL (a Settings field) applies to each resource. Anything unlisted gets the short task TTL.
_LONG, _TASK, _COMMENT = "ttl_long_seconds", "ttl_task_seconds", "ttl_comment_seconds"
TTL_FIELD: dict[str, str] = {
    **{r: _LONG for r in ("portals", "projects", "project", "users", "tags", "tasklists", "tasklist",
                          "milestones", "milestone")},
    **{r: _TASK for r in ("tasks", "task", "bugs", "bug", "search")},
    **{r: _COMMENT for r in ("task_comments", "bug_comments", "task_attachments", "bug_attachments",
                             "task_status_history")},
}


def default_cache_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path(env.get("HOME") or Path.home()) / ".cache"
    return root / "zoho-axi" / "cache.sqlite"


# The derived index: one table per kind, each (portal, project, id) -> name. Users and tags are
# portal-wide (project ""), tasklists and milestones belong to a project.
INDEX_TABLES = {"user": "index_users", "tag": "index_tags", "tasklist": "index_tasklists",
                "milestone": "index_milestones"}
_INDEX_DDL = "".join(
    f"CREATE TABLE IF NOT EXISTS {t} (portal TEXT NOT NULL, project TEXT NOT NULL, id TEXT NOT NULL,"
    " name TEXT NOT NULL, fetched_at REAL NOT NULL, PRIMARY KEY (portal, project, id)) WITHOUT ROWID;\n"
    for t in INDEX_TABLES.values()
)

_SCHEMA = f"""
{_INDEX_DDL}
CREATE TABLE IF NOT EXISTS task_keys (
    portal TEXT NOT NULL, project TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
    id TEXT NOT NULL, fetched_at REAL NOT NULL, PRIMARY KEY (portal, project, kind, key)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS entries (
    portal TEXT NOT NULL, project TEXT NOT NULL, resource TEXT NOT NULL,
    ident TEXT NOT NULL, params_hash TEXT NOT NULL,
    payload TEXT NOT NULL, fetched_at REAL NOT NULL,
    PRIMARY KEY (portal, project, resource, ident, params_hash)
) WITHOUT ROWID;
PRAGMA user_version = {SCHEMA_VERSION};
"""


class CacheStore:
    """The SQLite file. Every failure inside is absorbed: `warnings` gets a line, the call returns
    "nothing cached", and a corrupt file switches the store off for this process (`disabled`)."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time, busy_timeout_ms: int = 5000):
        self.path = Path(path)
        self.warnings: list[str] = []
        self.disabled = False
        self._clock = clock
        self._busy_ms = busy_timeout_ms
        self._conn: sqlite3.Connection | None = None

    def now(self) -> float:
        return self._clock()

    # --- plumbing ---

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            logger.warning("%s", message)

    def _connect(self) -> sqlite3.Connection | None:
        if self.disabled:
            return None
        if self._conn is not None:
            return self._conn
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600))
            os.chmod(self.path, 0o600)
            conn = self._open()
        except (sqlite3.Error, OSError) as exc:
            self._failed(exc)
            return None
        self._conn = conn
        return conn

    def _open(self) -> sqlite3.Connection:
        """Connect and make sure the schema exists. Switching to WAL and creating tables take locks
        that SQLite's busy timeout does not always cover, so a locked database is retried here until
        the timeout is spent (two processes starting on a brand-new cache is the usual cause)."""
        deadline = time.monotonic() + self._busy_ms / 1000
        while True:
            conn = sqlite3.connect(self.path, timeout=self._busy_ms / 1000, isolation_level=None)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, SCHEMA_VERSION):
                    raise sqlite3.DatabaseError(f"unsupported cache schema version {version}")
                if version == 0:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        for statement in _SCHEMA.split(";"):
                            if statement.strip():
                                conn.execute(statement)
                        conn.execute("COMMIT")
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
                return conn
            except sqlite3.OperationalError as exc:
                conn.close()
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
            except BaseException:
                conn.close()
                raise

    def _failed(self, exc: BaseException) -> None:
        transient = isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()
        if not transient:
            self.disabled = True
            self.close()
        reason = "locked by another process" if transient else f"unusable ({type(exc).__name__})"
        self._warn(f"cache {reason}; continuing without it. `zoho-axi cache clear --yes` resets it.")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    # --- entries ---

    def read(self, key: tuple[str, str, str, str, str]) -> tuple[Any, float] | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT payload, fetched_at FROM entries WHERE portal=? AND project=? AND resource=?"
                " AND ident=? AND params_hash=?", key,
            ).fetchone()
            return (json.loads(row[0]), row[1]) if row else None
        except (sqlite3.Error, ValueError) as exc:
            self._failed(exc)
            return None

    def write(self, key: tuple[str, str, str, str, str], payload_json: str) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?)",
                (*key, payload_json, self.now()),
            )
        except sqlite3.Error as exc:
            self._failed(exc)

    # --- derived index ---

    def index_put(self, kind: str, portal: str, project: str, rows: list[tuple[str, str]], *, replace: bool) -> None:
        """Record (id, name) pairs. `replace` means `rows` is the complete list for this scope, so
        anything not in it has gone away upstream and is dropped."""
        table = INDEX_TABLES[kind]
        conn = self._connect()
        if conn is None:
            return
        now = self.now()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if replace:
                    conn.execute(f"DELETE FROM {table} WHERE portal=? AND project=?", (portal, project))
                conn.executemany(
                    f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?)",
                    [(portal, project, i, n, now) for i, n in rows],
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        except sqlite3.Error as exc:
            self._failed(exc)

    def index_rows(self, kind: str, portal: str, project: str) -> list[tuple[str, str, float]]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            return conn.execute(
                f"SELECT id, name, fetched_at FROM {INDEX_TABLES[kind]} WHERE portal=? AND project=?",
                (portal, project),
            ).fetchall()
        except sqlite3.Error as exc:
            self._failed(exc)
            return []

    def key_put(self, portal: str, project: str, kind: str, key: str, ident: str) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute("INSERT OR REPLACE INTO task_keys VALUES (?,?,?,?,?,?)",
                         (portal, project, kind, key.upper(), ident, self.now()))
        except sqlite3.Error as exc:
            self._failed(exc)

    def key_list(self, portal: str, project: str) -> list[tuple[str, str]]:
        """(kind, key) of every remembered task/bug key of the project, however old: a prefix does not expire."""
        conn = self._connect()
        if conn is None:
            return []
        try:
            return conn.execute("SELECT kind, key FROM task_keys WHERE portal=? AND project=? ORDER BY key",
                                (portal, project)).fetchall()
        except sqlite3.Error as exc:
            self._failed(exc)
            return []

    def key_get(self, portal: str, project: str, kind: str, key: str) -> tuple[str, float] | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT id, fetched_at FROM task_keys WHERE portal=? AND project=? AND kind=? AND key=?",
                               (portal, project, kind, key.upper())).fetchone()
            return (row[0], row[1]) if row else None
        except sqlite3.Error as exc:
            self._failed(exc)
            return None


def _key(resource: str, params: Mapping[str, str] | None, ids: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    portal = str(ids.get("portal", ""))
    project = str(ids.get("project", ""))
    ident = ",".join(f"{k}={ids[k]}" for k in sorted(ids) if k not in ("portal", "project"))
    blob = json.dumps(dict(params or {}), sort_keys=True, separators=(",", ":"))
    return portal, project, resource, ident, hashlib.sha256(blob.encode()).hexdigest()[:16]


_INDEX_KIND = {"users": "user", "tags": "tag", "tasklists": "tasklist", "milestones": "milestone"}
_PORTAL_WIDE = ("user", "tag")
_KEY_KIND = {"task": "task", "tasks": "task", "bug": "bug"}


def _display_name(record: Mapping[str, Any]) -> str:
    for field in ("name", "display_name", "full_name"):
        if isinstance(record.get(field), str) and record[field].strip():
            return record[field].strip()
    return ""


def _pack(value: Any) -> dict:
    if isinstance(value, list):
        return {"many": True, "has_more": getattr(value, "has_more", None), "rows": list(value)}
    return {"many": False, "row": value}


def _unpack(data: dict) -> Any:
    if data["many"]:
        page = Page(data["rows"])
        page.has_more = data["has_more"]
        return page
    return data["row"]


class CachedGateway:
    def __init__(self, gateway: ZohoGateway, store: CacheStore, settings: Settings, *, fresh: bool = False):
        self._gateway = gateway
        self._store = store
        self._settings = settings
        self._fresh = fresh
        # Never persist a response that happens to contain a credential we know about.
        self._secrets = tuple(s for s in (settings.client_secret, settings.refresh_token) if s)

    @property
    def request_count(self) -> int:
        return self._gateway.request_count

    @property
    def warnings(self) -> list[str]:
        return self._store.warnings

    def _ttl(self, resource: str) -> int:
        return getattr(self._settings, TTL_FIELD.get(resource, _TASK))

    def get(self, resource: str, /, *, params: Mapping[str, str] | None = None, **ids: Any):
        key = _key(resource, params, ids)
        if not self._fresh:
            hit = self._store.read(key)
            if hit is not None and self._store.now() - hit[1] < self._ttl(resource):
                return _unpack(hit[0])
        value = self._gateway.get(resource, params=params, **ids)
        text = json.dumps(_pack(value), separators=(",", ":"))
        if not any(secret in text for secret in self._secrets):
            self._store.write(key, text)
            self._learn(resource, params, ids, value)
        return value

    def close(self) -> None:
        self._store.close()

    def index(self, *, portal: str, project: str | None = None) -> "Index":
        return Index(self._store, self._settings, portal=portal, project=project)

    def _learn(self, resource: str, params: Mapping[str, str] | None, ids: Mapping[str, Any], value: Any) -> None:
        """Feed the derived index from what was just fetched (fresh runs included)."""
        portal, project = str(ids.get("portal", "")), str(ids.get("project", ""))
        kind = _INDEX_KIND.get(resource)
        if kind is not None and isinstance(value, list):
            rows = [(r["id"], name) for r in value if isinstance(r, dict) and r.get("id")
                    for name in [_display_name(r)] if name]
            complete = not params and getattr(value, "has_more", None) is not True
            self._store.index_put(kind, portal, "" if kind in _PORTAL_WIDE else project, rows, replace=complete)
        key_kind = _KEY_KIND.get(resource)
        if key_kind is not None:
            for record in value if isinstance(value, list) else [value]:
                if isinstance(record, dict) and record.get("id") and isinstance(record.get("key"), str) and record["key"]:
                    self._store.key_put(portal, project, key_kind, record["key"], str(record["id"]))


def _norm_name(text: str) -> str:
    return " ".join(text.casefold().split())


class Index:
    """Name -> id resolution over the cached users / tags / tasklists / milestones, plus a memo of
    task and bug keys. Reads only the local database: never a network call, so a cold index simply
    answers None and the caller decides whether to fetch the list.

    `resolve(kind, text)`: kind is user, tag, tasklist, milestone (plural also accepted), or task /
    bug (then `text` is a human key like PROJ1-T1000). Names match case- and whitespace-insensitively:
    an exact id first, then an exact name, then a unique prefix, then a unique substring. Anything
    ambiguous, unknown, or older than the long TTL gives None."""

    def __init__(self, store: CacheStore, settings: Settings, *, portal: str, project: str | None = None):
        self._store = store
        self._settings = settings
        self._portal = str(portal)
        self._project = "" if project is None else str(project)

    def _scope(self, kind: str) -> str:
        return "" if kind in _PORTAL_WIDE else self._project

    def _kind(self, kind: str) -> str:
        singular = kind[:-1] if kind.endswith("s") else kind
        if singular not in INDEX_TABLES and singular not in ("task", "bug"):
            raise ValueError(f"unknown index kind {kind!r}")
        return singular

    def _fresh(self, fetched_at: float) -> bool:
        return self._store.now() - fetched_at < self._settings.ttl_long_seconds

    def _rows(self, kind: str) -> list[tuple[str, str]]:
        rows = self._store.index_rows(kind, self._portal, self._scope(kind))
        return [(i, n) for i, n, at in rows if self._fresh(at)]

    def resolve(self, kind: str, name_or_id: str) -> str | None:
        kind = self._kind(kind)
        if kind in ("task", "bug"):
            return self.resolve_key(name_or_id, kind=kind)
        wanted = _norm_name(str(name_or_id))
        rows = self._rows(kind)
        if not wanted or not rows:
            return None
        for ident, _ in rows:
            if ident == str(name_or_id).strip():
                return ident
        for match in (lambda n: n == wanted, lambda n: n.startswith(wanted), lambda n: wanted in n):
            found = {i for i, n in rows if match(_norm_name(n))}
            if found:
                return found.pop() if len(found) == 1 else None
        return None

    def name_of(self, kind: str, ident: str) -> str | None:
        """The display name for an id (e.g. for `owner: Asha K`), or None when not warm."""
        kind = self._kind(kind)
        return next((n for i, n in self._rows(kind) if i == str(ident)), None)

    def names(self, kind: str) -> list[str]:
        """The display names of every fresh entry of `kind` (e.g. milestones), for matching text against them."""
        return [n for _, n in self._rows(self._kind(kind))]

    def known_keys(self) -> list[tuple[str, str]]:
        """(kind, KEY) pairs remembered for this project, e.g. ("task", "DM1-T2"): what `resolve` learns the
        project prefix(es) and the bug key letter from. Local database only."""
        return self._store.key_list(self._portal, self._project)

    def remember_key(self, key: str, ident: str, *, kind: str = "task") -> None:
        self._store.key_put(self._portal, self._project, self._kind(kind), key, str(ident))

    def resolve_key(self, key: str, *, kind: str = "task") -> str | None:
        hit = self._store.key_get(self._portal, self._project, self._kind(kind), key.strip())
        return hit[0] if hit and self._fresh(hit[1]) else None


# --- `zoho-axi cache status|clear` ---------------------------------------------------------------

def _human_size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _files(path: Path) -> list[Path]:
    return [p for p in (path, *(path.with_name(path.name + sfx) for sfx in ("-wal", "-shm", "-journal"))) if p.exists()]


def cache_status(path: Path | None = None, *, clock: Callable[[], float] = time.time) -> Result:
    """Size, entry counts and the oldest entry. Read-only: a missing cache stays missing and an
    unreadable one is reported as a warning (and fixed by `cache clear`)."""
    path = default_cache_path() if path is None else Path(path)
    counts: dict[str, Any] = {"entries": 0}
    payload: dict[str, Any] = {"path": str(path), "size": _human_size(sum(p.stat().st_size for p in _files(path)))}
    warnings: list[str] = []
    if path.exists():
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            try:
                count, oldest = conn.execute("SELECT COUNT(*), MIN(fetched_at) FROM entries").fetchone()
                counts["entries"] = count
                for kind, table in INDEX_TABLES.items():
                    counts[f"{kind}s"] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                counts["keys"] = conn.execute("SELECT COUNT(*) FROM task_keys").fetchone()[0]
            finally:
                conn.close()
            if oldest is not None:
                payload["oldest"] = datetime.fromtimestamp(oldest, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except sqlite3.Error as exc:
            warnings.append(f"cache unreadable ({type(exc).__name__}); run `zoho-axi cache clear --yes` to reset it")
    else:
        payload["size"] = "not created yet"
    return Result("cache", payload, counts=counts, warnings=warnings,
                  help=[Hint("cache clear", {"yes": True})] if path.exists() else [])


def cache_clear(yes: bool = False, path: Path | None = None) -> Result:
    """Delete the cache file (which also drops the derived index). Never prompts: without `yes` it
    refuses with a usage error."""
    if not yes:
        raise UsageError(
            "usage.confirmation_required", "`cache clear` deletes every cached response and the derived index.",
            hint="Re-run with --yes (this command never prompts).",
        )
    path = default_cache_path() if path is None else Path(path)
    removed = cache_status(path).counts["entries"]
    for file in _files(path):
        try:
            file.unlink()
        except FileNotFoundError:
            pass
    return Result("cache", {"path": str(path), "cleared": removed})
