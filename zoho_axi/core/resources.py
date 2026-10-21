"""The single table that maps each Zoho resource to its v3 path and optional legacy path, plus the
normalization that collapses Zoho's response-shape and field-casing variants at the edge.

Callers never build URLs or branch on casing: they ask the gateway for a resource by name.
Pure functions and data only; no I/O.
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Resource:
    v3: str | None  # path under the v3 base; None when Zoho has no v3 equivalent
    legacy: str | None = None  # path under /restapi, used on a v3 404 (or alone when v3 is None)
    many: bool = False  # collection (list of entities) or a single entity
    keys: tuple[str, ...] = ()  # keys a response may wrap its list under
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # canonical field -> fallback spellings

    @property
    def id_names(self) -> frozenset[str]:
        paths = (p for p in (self.v3, self.legacy) if p)
        return frozenset(
            name for path in paths for _, name, _, _ in string.Formatter().parse(path) if name
        )


_P = "/portal/{portal}"
_PR = "/portal/{portal}/projects/{project}"

# Attachment records come back with FILENAME / file_name / name, CONTENT_TYPE / mime_type / type,
# DOWNLOAD_URL / downloadUrl / url ... depending on endpoint and API version. Ported from the legacy
# context_builders `_first` helper; the first non-empty spelling wins, in this order.
_ATTACHMENT_ALIASES = {
    "file_name": ("filename", "name"),
    "content_type": ("mime_type", "type"),
    "download_url": ("url", "link", "file_url"),
}

RESOURCES: dict[str, Resource] = {
    "portals": Resource("/portals", many=True, keys=("portals",)),
    "projects": Resource(f"{_P}/projects", f"{_P}/projects/", many=True, keys=("projects",)),
    "project": Resource(_PR, keys=("projects",)),
    # No confirmed v3 equivalent; needs the ZohoProjects.search.READ scope. params: search_term, module.
    "search": Resource(None, f"{_PR}/search", many=True, keys=("tasks", "bugs")),
    "users": Resource(f"{_P}/users", many=True, keys=("users",)),
    "tags": Resource(f"{_P}/tags", many=True, keys=("tags",)),
    "tasklists": Resource(f"{_PR}/tasklists", many=True, keys=("tasklists",)),
    "tasklist": Resource(f"{_PR}/tasklists/{{tasklist}}", keys=("tasklists",)),
    "milestones": Resource(f"{_PR}/milestones", f"{_PR}/milestones/", many=True, keys=("milestones",)),
    "milestone": Resource(f"{_PR}/milestones/{{milestone}}", f"{_PR}/milestones/{{milestone}}/", keys=("milestones",)),
    "tasks": Resource(f"{_PR}/tasks", many=True, keys=("tasks",)),
    "task": Resource(f"{_PR}/tasks/{{task}}", keys=("tasks",)),
    "task_comments": Resource(
        f"{_PR}/tasks/{{task}}/comments", f"{_PR}/tasks/{{task}}/comments/", many=True, keys=("comments",)
    ),
    "task_attachments": Resource(
        f"{_PR}/tasks/{{task}}/attachments", f"{_PR}/tasks/{{task}}/attachments/", many=True,
        keys=("attachments", "attachment"), aliases=_ATTACHMENT_ALIASES,
    ),
    # Ignores task scoping server-side: callers filter by task id themselves.
    "task_status_history": Resource(f"{_PR}/taskstatushistory", many=True, keys=("task_status_history", "history")),
    "bug": Resource(f"{_PR}/bugs/{{bug}}", keys=("bugs",)),
    "bug_comments": Resource(
        f"{_PR}/bugs/{{bug}}/comments", f"{_PR}/bugs/{{bug}}/comments/", many=True, keys=("comments",)
    ),
    "bug_attachments": Resource(
        f"{_PR}/bugs/{{bug}}/attachments", f"{_PR}/bugs/{{bug}}/attachments/", many=True,
        keys=("attachments", "attachment"), aliases=_ATTACHMENT_ALIASES,
    ),
}


class Page(list):
    """A normalized collection. `has_more` is True/False when Zoho said so (`page_info`), else None."""

    has_more: bool | None = None


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", key).lower()


def normalize_record(record: Mapping[str, Any], aliases: Mapping[str, tuple[str, ...]] | None = None) -> dict:
    """Top-level keys to lower snake_case (FILENAME -> filename, downloadUrl -> download_url), then
    each canonical field takes the first non-empty of its spellings and the spellings are dropped.
    `id` is always a string (Zoho mixes numbers and strings). Nested values are left alone."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        name = _snake(str(key))
        if name not in out or (not out[name] and value):
            out[name] = value
    for canonical, fallbacks in (aliases or {}).items():
        spellings = (canonical, *fallbacks)
        chosen = next((out[s] for s in spellings if out.get(s)), out.get(canonical))
        for spelling in fallbacks:
            out.pop(spelling, None)
        if chosen is not None:
            out[canonical] = chosen
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    return out


def _records(payload: Any, keys: tuple[str, ...]) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in (*keys, "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def normalize(resource: Resource, payload: Any) -> Any:
    """Turn a decoded response body (or None for an empty one) into a `Page` of records for a
    collection resource, or one record (possibly empty) for a single-entity resource."""
    if resource.many:
        page = Page(
            normalize_record(r, resource.aliases) for r in _records(payload, resource.keys) if isinstance(r, dict)
        )
        info = payload.get("page_info") if isinstance(payload, dict) else None
        flag = info.get("has_next_page") if isinstance(info, dict) else None
        page.has_more = flag if isinstance(flag, bool) else None
        return page
    if isinstance(payload, dict):
        # Some endpoints wrap a single entity in a one-element list: {"tasks": [{...}]}.
        wrapped = next((payload[k] for k in resource.keys if isinstance(payload.get(k), list)), None)
        if wrapped is not None:
            first = next((r for r in wrapped if isinstance(r, dict)), {})
            return normalize_record(first, resource.aliases)
        return normalize_record(payload, resource.aliases)
    if isinstance(payload, list):
        first = next((r for r in payload if isinstance(r, dict)), {})
        return normalize_record(first, resource.aliases)
    return {}
