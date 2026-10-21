"""Pure helpers that turn the gateway's normalized Zoho records into the flat display rows the render
schemas expect. Zoho spells the same idea several ways (v3 `status{name,is_closed_type}` vs legacy
`status: "completed"`, `owners_and_work.owners[]` vs `details.owners[]` vs `owner_name`, ISO dates vs
`MM-dd-yyyy` vs epoch milliseconds, booleans as the string "false"), so every reader here is tolerant
and returns "" rather than raising. No I/O.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_US_DATE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})")
_NAME_KEYS = ("name", "full_name", "display_name")  # users: display_name can be just a first name
_UNASSIGNED = {"unassigned user", "unassigned"}
_CLOSED_WORDS = {"closed", "completed", "complete", "done", "resolved"}


def truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def person_name(value: Any) -> str:
    """A display name from a person object, a bare string, or nothing."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in _NAME_KEYS:
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    return ""


def owner_names(record: Mapping[str, Any]) -> list[str]:
    """Assignee names across v3 (`owners_and_work.owners`), legacy (`details.owners`, `owner_name`) and
    single-owner objects. Zoho's "Unassigned User" placeholder is not a person."""
    people: list[Any] = []
    work = record.get("owners_and_work")
    if isinstance(work, Mapping) and isinstance(work.get("owners"), list):
        people = work["owners"]
    elif isinstance(record.get("details"), Mapping) and isinstance(record["details"].get("owners"), list):
        people = record["details"]["owners"]
    elif record.get("owner"):
        people = [record["owner"]]
    elif record.get("owner_name"):
        people = [record["owner_name"]]
    elif record.get("assignee"):
        people = record["assignee"] if isinstance(record["assignee"], list) else [record["assignee"]]
    names = []
    for person in people:
        if isinstance(person, Mapping) and str(person.get("zuid", "")) == "0":
            continue
        name = person_name(person)
        if name and name.lower() not in _UNASSIGNED:
            names.append(name)
    return names


def status_name(record: Mapping[str, Any]) -> str:
    status = record.get("status")
    if isinstance(status, Mapping):
        return str(status.get("name") or status.get("type") or "").strip()
    return str(status).strip() if status not in (None, "") else ""


def is_closed(record: Mapping[str, Any]) -> bool:
    status = record.get("status")
    if isinstance(status, Mapping):
        if "is_closed_type" in status:
            return truthy(status["is_closed_type"])
        if str(status.get("type", "")).lower() in _CLOSED_WORDS:
            return True
    elif isinstance(status, str) and status.strip().lower() in _CLOSED_WORDS:
        return True
    return any(truthy(record.get(k)) for k in ("is_completed", "completed"))


def norm_date(value: Any) -> str:
    """`YYYY-MM-DD` from an ISO date/time, `MM-dd-yyyy[ hh:mm AM]` or epoch seconds/milliseconds; text
    that is none of those is returned unchanged and anything empty becomes ""."""
    if value in (None, "", 0):
        return ""
    text = str(value).strip()
    if text.isdigit() and len(text) >= 10:
        seconds = int(text) / (1000 if len(text) >= 12 else 1)
        return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d")
    if m := _ISO_DATE.match(text):
        return m.group(1)
    if m := _US_DATE.match(text):
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    return text


def due_date(record: Mapping[str, Any]) -> str:
    for key in ("end_date", "due_date", "end_date_long", "due_date_long"):
        if record.get(key):
            return norm_date(record[key])
    return ""


def task_key(record: Mapping[str, Any]) -> str:
    """The human key (`PROJ1-T1000`): v3 `prefix`, legacy `key`; the id when neither is present."""
    for name in ("prefix", "key"):
        if isinstance(record.get(name), str) and record[name].strip():
            return record[name].strip()
    return str(record.get("id", ""))


def task_row(record: Mapping[str, Any]) -> dict[str, str]:
    return {
        "key": task_key(record),
        "title": str(record.get("name") or record.get("title") or ""),
        "status": status_name(record),
        "owner": ", ".join(owner_names(record)),
        "due": due_date(record),
    }


def count_key(label: str) -> str:
    """`In Progress` -> `in_progress`: a status as a `k=v` count key."""
    return "_".join(label.lower().split()) or "none"


def tally(labels: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in labels:
        key = count_key(label)
        counts[key] = counts.get(key, 0) + 1
    return counts
