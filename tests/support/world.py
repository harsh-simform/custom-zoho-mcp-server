"""A tiny CLI "world" for command tests (T10): the product seam, `main(argv, env, stdout, stderr)` run
in-process, with the Zoho wire replaced by a ReplayTransport-backed gateway injected the same way
tests inject a registry. T04's recorded fixtures will supersede the builders below; until then every
body is an invented-but-plausible shape modelled on the research notes (v3 tasks list with `prefix`,
`owners_and_work`, `status{is_closed_type}`; projects as a bare array; legacy milestones; users with
`display_name`/`role{name}`; tags with `color_hexcode`).

Each `run` builds a fresh gateway (request counter at 0) over the transport it is given; the SQLite
cache lives under the world's tmp HOME, so a second `run` is "warm" and an empty transport proves it
made no request.
"""
from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

from support.factories import API, LEGACY
from support.replay import ReplayTransport

from zoho_projects_axi.cli.main import main
from zoho_projects_axi.core.config import load_settings
from zoho_projects_axi.core.gateway import ZohoGateway

PORTAL = "1100"
PROJECT = "2200"
P = f"{API}/portal/{PORTAL}"
PR = f"{P}/projects/{PROJECT}"
TODAY = date(2026, 10, 21)

CREDS = {
    "ZOHO_PROJECTS_AXI_CLIENT_ID": "cid-SECRET-1",
    "ZOHO_PROJECTS_AXI_CLIENT_SECRET": "csecret-SECRET-2",
    "ZOHO_PROJECTS_AXI_REFRESH_TOKEN": "refresh-SECRET-3",
}


def token_count(text: str) -> float:
    return len(text) / 4  # the deterministic proxy T06's tests use


class World:
    def __init__(self, tmp_path: Path, *, configured: bool = True, **env_extra: str):
        self.tmp = tmp_path
        self.env = {"HOME": str(tmp_path), "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env"), **CREDS}
        if configured:
            self.env.update({"ZOHO_PROJECTS_AXI_PORTAL_ID": PORTAL, "ZOHO_PROJECTS_AXI_PROJECT_ID": PROJECT})
        self.env.update(env_extra)
        self.last_replay: ReplayTransport | None = None

    @property
    def config_path(self) -> Path:
        return self.tmp / ".config" / "zoho-projects-axi" / "config.toml"

    def run(self, argv, replay: ReplayTransport | None = None, *, today: date = TODAY, env: dict | None = None,
            stdin=None, registry=None):
        """(exit code, stdout, stderr). stderr carries `requests: N` because every run adds --debug.
        `stdin` is handed to `main` (only a command that declares it reads it); `registry` swaps the command table."""
        replay = replay if replay is not None else ReplayTransport()
        self.last_replay = replay
        env = {**self.env, **(env or {})}
        settings = load_settings(env=env, legacy_env_file=self.tmp / "none.env")
        gateway = ZohoGateway(settings, transport=replay, token_provider=lambda: "tok-abc", sleep=lambda s: None)
        out, err = io.StringIO(), io.StringIO()
        extra = {"registry": registry} if registry is not None else {}
        code = main([*argv, "--debug"], env, out, err, gateway=gateway, today=today, stdin=stdin, **extra)
        return code, out.getvalue().rstrip("\n"), err.getvalue()

    def requests(self, err: str) -> int:
        return int(err.strip().splitlines()[-1].split(":")[1])


def replay_of(*entries) -> ReplayTransport:
    """entries: (url, body) or (url, body, params) or (url, body, params, status)."""
    replay = ReplayTransport()
    for entry in entries:
        url, body, *rest = entry
        params = rest[0] if rest else None
        status = rest[1] if len(rest) > 1 else 200
        replay.get(url, json=body, params=params, status=status)
    return replay


# --- body builders ------------------------------------------------------------------------------

def task(n, name, status="Open", owner="Alex Sample", closed=False, end=None, tasklist="9001", **extra):
    owners = [{"zuid": 7, "zpuid": "5001", "name": owner}] if owner else [
        {"zuid": 0, "name": "Unassigned User", "email": "Unassigned User", "last_name": ""}]
    rec = {"id": f"30{n:02d}", "prefix": f"DM1-T{n}", "name": name, "tasklist": {"id": tasklist},
           "status": {"id": "1", "name": status, "is_closed_type": closed}, "is_completed": closed,
           "owners_and_work": {"owners": owners}, "priority": "none", "depth": 0}
    if end:
        rec["end_date"] = end
    rec.update(extra)
    return rec


def tasks_body(*records, has_next=False):
    return {"page_info": {"page": 1, "per_page": 100, "page_count": len(records), "has_next_page": has_next},
            "tasks": list(records)}


def project_rec(id_="2200", name="Demo Website Redesign", status="In Progress", owner="Alex Sample", **extra):
    rec = {"id": id_, "name": name, "key": "54", "description": "<p>Redesign the demo site.</p>",
           "status": {"id": "9", "name": status, "is_closed_type": "false"},
           "owner": {"id": "5001", "name": owner}, "start_date": "2026-01-05", "end_date": "2026-09-30",
           "tasks": {"open_count": 15, "closed_count": 10}}
    rec.update(extra)
    return rec


def tasklists_body(*pairs):
    return {"page_info": {"page": 1, "per_page": 200, "has_next_page": False},
            "tasklists": [{"id": i, "name": n, "status": "active"} for i, n in pairs]}


def legacy_milestones(*rows):
    """(id, name, end_date 'MM-dd-yyyy hh:mm AM', completed?)"""
    return {"milestones": [
        {"id": int(i), "name": n, "owner_name": "Bea Fixture", "end_date": end,
         "status": "completed" if done else "notcompleted"} for i, n, end, done in rows]}


def users_body(*rows):
    return {"page_info": {"per_page": 100, "has_next_page": False, "count": len(rows), "page": 1},
            "users": [{"id": i, "zuid": "7", "display_name": n, "full_name": n, "email": f"{n.split()[0].lower()}@x.test",
                       "role": {"name": role, "id": "1"}, "status": "active"} for i, n, role in rows]}


def tags_body(*rows):
    return {"page_info": {"per_page": 100, "has_next_page": False, "count": len(rows), "page": 1},
            "tags": [{"id": i, "name": n, "color_hexcode": "#0995ba", "usage_count": 1} for i, n in rows]}


def tasklist_filter(field, value):
    return json.dumps({"criteria": [{"field_name": field, "criteria_condition": "is", "value": [value]}],
                       "pattern": "1"})


LEGACY_MILESTONES_URL = f"{LEGACY}/portal/{PORTAL}/projects/{PROJECT}/milestones/"
