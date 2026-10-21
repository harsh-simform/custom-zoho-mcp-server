"""`zoho-axi people [--users] [--tags]`: the portal's users and tags as lean lists. No flag shows both.

Users and tags are portal-wide and long-lived, so they come from the cache; fetching them also fills the
derived index (`owner: Asha K` rendering and name -> id lookups). Each list is capped (`--limit`, default
50) with a hint, because a portal can have hundreds of users.
"""
from __future__ import annotations

from ..result import Hint, Result, Truncation
from . import _shape
from ._scope import portal_of
from .base import Context
from .tasks import int_param

DEFAULT_LIMIT = 50


def _user_row(record: dict) -> dict:
    role = record.get("role")
    return {
        "id": str(record.get("id", "")),
        "name": _shape.person_name(record) or str(record.get("email", "")),
        "role": role.get("name", "") if isinstance(role, dict) else str(role or ""),
        "email": str(record.get("email", "")),
    }


def _tag_row(record: dict) -> dict:
    return {"id": str(record.get("id", "")), "name": str(record.get("name", "")),
            "color": str(record.get("color_hexcode", ""))}


def people(ctx: Context) -> Result:
    limit = int_param(ctx, "limit", DEFAULT_LIMIT)
    want_users, want_tags = bool(ctx.params.get("users")), bool(ctx.params.get("tags"))
    if not want_users and not want_tags:
        want_users = want_tags = True
    portal = portal_of(ctx)

    payload: dict = {}
    counts: dict = {}
    result = Result("people", payload, counts=counts)
    for wanted, resource, shape in ((want_users, "users", _user_row), (want_tags, "tags", _tag_row)):
        if not wanted:
            continue
        records = ctx.data.get(resource, portal=portal)
        rows = [shape(r) for r in records]
        counts[resource] = f"{len(rows)}+" if records.has_more is True else len(rows)
        payload[resource] = rows[:limit]
        if len(rows) > limit:
            result.truncations.append(Truncation(resource, len(rows) - limit, "rows", {"limit": len(rows)}))
    if payload.get("users"):
        result.help.append(Hint("tasks", {"owner": payload["users"][0]["name"]}))
    return result
