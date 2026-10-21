"""The one table that decides which fields an entity shows by default (AXI principle 2).

List rows carry 3-4 fields; a detail view about 8-9. `--full` ignores these and shows every field the
gateway returned. Field names are the already-normalized ones from the Zoho gateway (T02). Commands
extend or adjust this table; nothing else in the code base lists display fields.

Entries for task/comment are taken from the T05/T08 specs; bug, history and attachment are
invented-but-plausible and should be confirmed against real fixtures by T08/T09. The T10 entries
(project, default, tasklist, milestone, user, tag, people, home, due_task) are what the `project`,
`tasks`, `people` and `home` commands emit, built against the research-note shapes rather than
recorded responses: confirm them when T04's fixtures exist.

The gateway only normalizes casing, not meaning, so commands map Zoho's fields (`prefix` -> `key`,
`name` -> `title`, `status.name` -> `status`, `owners_and_work.owners[].name` -> `owner`) into these
flat names themselves (see commands/_shape.py).
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
    verbatim: bool = False          # rows keep their whitespace (a diff's indentation); the default collapses it


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
        list_fields=("id", "name", "status", "portal"),
        detail_fields=("name", "status", "owner", "start", "end", "next_milestone"),
        id_field="id",  # `description` is deliberately not listed: it only appears with --full
        sections={"tasklists": "tasklist", "milestones": "milestone"},  # present only with `project --lists`
    ),
    # `project use`: what was written to the config file (T10).
    Schema("default", "defaults", list_fields=("project", "name"), detail_fields=("portal", "name", "config"),
           id_field="project"),
    Schema("tasklist", "tasklists", list_fields=("id", "name", "status")),
    Schema("milestone", "milestones", list_fields=("id", "name", "due", "status")),
    Schema("user", "users", list_fields=("id", "name", "role")),
    Schema("tag", "tags", list_fields=("id", "name")),
    # `people`: users and tags side by side, one section each (T10).
    Schema("people", "peoples", list_fields=("name",), sections={"users": "user", "tags": "tag"}),
    # `zoho-axi cache status|clear` (T07): a detail view; the tallies ride in `counts`.
    Schema("cache", "caches", list_fields=("path",), detail_fields=("path", "size", "oldest", "cleared")),
    # T06 commands
    Schema("auth", "auths", list_fields=("credentials", "token"), detail_fields=(
        "config_file", "config_file_exists", "credentials", "token", "token_error", "settings")),
    Schema("step", "steps", list_fields=("text",)),
    # `home` (T10): the default project at a glance; offline (credentials missing) it carries portal/credentials/note.
    Schema("home", "homes", list_fields=("project",),
           detail_fields=("portal", "project", "name", "status", "credentials", "note"),
           sections={"due": "due_task"}),
    Schema("due_task", "due_tasks", list_fields=("key", "title", "due", "owner")),
    # T14: `hooks install|uninstall`, `skill install` (the diff of the edit, one row per line) and `session-start`.
    Schema("hooks", "hooks", list_fields=("text",), detail_fields=("file", "status", "backup"),
           sections={"diff": "diff_line"}),
    Schema("skill", "skills", list_fields=("text",), detail_fields=("file", "change", "status", "backup"),
           sections={"diff": "diff_line"}),
    Schema("diff_line", "diff_lines", list_fields=("text",), verbatim=True),
    Schema("session", "sessions", list_fields=("project",), detail_fields=("project", "name", "status", "note")),
)}


def schema_for(entity: str) -> Schema:
    try:
        return SCHEMAS[entity]
    except KeyError:
        raise ValueError(f"no output schema for entity {entity!r}; add it to zoho_axi/core/schemas.py") from None
