"""Loader for the sanitized Zoho fixtures under tests/fixtures/zoho/<endpoint>/<name>.json.

A fixture file is one recorded scenario: `{"_provenance": {...}, "entries": [...]}` where each entry is
a request/response pair `{method, url, params?, status, json? | body_text? | body_base64?, content_type?}`.
`replay_for("task_comments/v3_404_legacy_ok")` turns one or more scenarios into the existing
`ReplayTransport` (tests/support/replay.py), which serves them by method+URL+params in the order
recorded and raises `UnrecordedRequest` naming the missing key for anything else.

Every file carries `_provenance.groups`: field-group -> "<marker>: detail" where marker is one of
`documented` (copied from Zoho's published API docs), `proven-by-legacy-code` (behavior the legacy
zoho_mcp code depends on) or `invented` (plausible synthetic) -- or `recorded` once a real, sanitized
recording replaces it. See tests/README.md.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .recording import PROVENANCE_MARKERS
from .replay import ReplayTransport

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "zoho"

# Canonical fake ids used by the synthetic fixtures; the recorder pins real ids to these so tests can
# name them (`gateway.get("task", portal=PORTAL, project=PROJECT, task=TASK)`).
PORTAL = "10000000001"
PROJECT = "20000000001"
TASK = "30000000001"  # has comments, attachments and history; association_info.has_subtasks is false
TASK_PARENT = "30000000005"  # association_info.has_subtasks is true
BUG = "40000000001"  # has an associated task
BUG_NO_TASK = "40000000002"
TASKLIST = "50000000001"
MILESTONE = "60000000001"
CANONICAL_IDS = {
    "portal": PORTAL,
    "project": PROJECT,
    "task": TASK,
    "bug": BUG,
    "tasklist": TASKLIST,
    "milestone": MILESTONE,
}
IDS = {"portal": PORTAL, "project": PROJECT}


def fixture_path(name: str) -> Path:
    return FIXTURE_ROOT / f"{name}.json"


def all_fixture_names() -> list[str]:
    return sorted(str(p.relative_to(FIXTURE_ROOT).with_suffix("")) for p in FIXTURE_ROOT.glob("*/*.json"))


def check_provenance(document: dict) -> list[str]:
    """Problems with a fixture's `_provenance` marker; empty when it is valid."""
    prov = document.get("_provenance")
    if not isinstance(prov, dict) or not isinstance(prov.get("groups"), dict) or not prov["groups"]:
        return ["missing _provenance.groups"]
    problems = []
    for group, text in prov["groups"].items():
        if not isinstance(text, str) or not text.startswith(PROVENANCE_MARKERS):
            problems.append(f"group {group!r} must start with one of {PROVENANCE_MARKERS}, got {text!r}")
    return problems


def load_fixture(name: str) -> dict:
    document = json.loads(fixture_path(name).read_text(encoding="utf-8"))
    problems = check_provenance(document)
    if problems:
        raise ValueError(f"fixture {name}: " + "; ".join(problems))
    return document


def _to_replay_entry(entry: dict) -> dict:
    out = {k: v for k, v in entry.items() if k in ("method", "url", "params", "status", "json", "content_type")}
    if "body_base64" in entry:
        out["body"] = base64.b64decode(entry["body_base64"])
    elif "body_text" in entry:
        out["body"] = entry["body_text"].encode()
    return out


def entries_for(*names: str) -> list[dict]:
    return [_to_replay_entry(e) for name in names for e in load_fixture(name)["entries"]]


def replay_for(*names: str) -> ReplayTransport:
    """A replay transport serving the named fixture scenarios (and nothing else)."""
    return ReplayTransport.from_entries(entries_for(*names))


def first_json(name: str) -> Any:
    """The decoded body of a fixture's first entry (handy for building expectations from the file)."""
    return load_fixture(name)["entries"][0].get("json")
