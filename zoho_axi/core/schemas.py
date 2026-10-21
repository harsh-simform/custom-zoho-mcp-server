"""The one table that decides which fields an entity shows by default (AXI principle 2).

List rows carry 3-4 fields; a detail view about 8-9. `--full` ignores these and shows every field the
gateway returned. Field names are the already-normalized ones from the Zoho gateway (T02). Commands
extend or adjust this table; nothing else in the code base lists display fields.

Entries for task/comment are taken from the T05/T08 specs; the rest (bug, history, attachment,
project, tasklist, milestone, user, tag) are invented-but-plausible and should be confirmed against
real fixtures by T08-T10.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Schema:
    name: str                       # singular label: the key of a detail's header line (`task: KEY`)
    plural: str                     # label of a list (`tasks[3]{...}:` / `tasks: 0 results`)
    list_fields: tuple[str, ...]    # columns of a row in a list or section
    detail_fields: tuple[str, ...] = ()   # short fields of a detail view, rendered before `counts`
    text_fields: tuple[str, ...] = ()     # long-text fields of a detail view, rendered after `counts`
    id_field: str | None = None     # detail header value; excluded from the field lines
    sections: Mapping[str, str] = field(default_factory=dict)  # payload key -> entity of its rows


SCHEMAS: dict[str, Schema] = {s.name: s for s in (
    Schema(
        "task", "tasks",
        list_fields=("key", "title", "status", "owner"),
        detail_fields=("title", "status", "owner", "priority", "due", "tasklist", "milestone", "tags"),
        text_fields=("description",),
        id_field="key",
        sections={"comments": "comment", "history": "history", "subtasks": "task",
                  "attachments": "attachment"},
    ),
    Schema(
        "bug", "bugs",
        list_fields=("key", "title", "status", "severity"),
        detail_fields=("title", "status", "severity", "owner", "reporter", "due", "milestone"),
        text_fields=("description",),
        id_field="key",
        sections={"comments": "comment", "attachments": "attachment"},
    ),
    Schema("comment", "comments", list_fields=("by", "at", "text")),
    Schema("history", "history", list_fields=("at", "by", "from", "to")),
    Schema("attachment", "attachments", list_fields=("name", "type", "size")),
    Schema(
        "project", "projects",
        list_fields=("id", "name", "status"),
        detail_fields=("name", "status", "owner", "start", "end"),
        text_fields=("description",),
        id_field="id",
    ),
    Schema("tasklist", "tasklists", list_fields=("id", "name", "open")),
    Schema("milestone", "milestones", list_fields=("id", "name", "due", "status")),
    Schema("user", "users", list_fields=("id", "name", "role")),
    Schema("tag", "tags", list_fields=("id", "name")),
    # `zoho-axi cache status|clear` (T07): a detail view; the tallies ride in `counts`.
    Schema("cache", "caches", list_fields=("path",), detail_fields=("path", "size", "oldest", "cleared")),
    # T06 commands (home is a placeholder until T10)
    Schema("auth", "auths", list_fields=("credentials", "token"), detail_fields=(
        "config_file", "config_file_exists", "credentials", "token", "token_error", "settings")),
    Schema("step", "steps", list_fields=("text",)),
    Schema("home", "homes", list_fields=("project",),
           detail_fields=("portal", "project", "credentials", "note")),
)}


def schema_for(entity: str) -> Schema:
    try:
        return SCHEMAS[entity]
    except KeyError:
        raise ValueError(f"no output schema for entity {entity!r}; add it to zoho_axi/core/schemas.py") from None
