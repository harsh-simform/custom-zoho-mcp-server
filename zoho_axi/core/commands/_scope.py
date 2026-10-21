"""Shared plumbing for the project-level commands: which project a call is about, name -> id lookups
through the derived index, and sub-fetches whose failure is a warning rather than a crash. Reads Zoho
only through `ctx.data` (the cache), so repeated lookups are free.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ..errors import AuthError, ConfigError, NotFoundError, RateLimitedError, UsageError, ZohoAxiError
from .base import Context

_RESOURCE = {"tasklist": "tasklists", "milestone": "milestones"}


def norm(text: Any) -> str:
    return " ".join(str(text).casefold().split())


def pick(records: Sequence[Mapping[str, Any]], text: str) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]]]:
    """The record `text` names: exact id, then exact name, unique prefix, unique substring (case- and
    whitespace-insensitive). Returns (record, []) on a hit, (None, candidates) when several match and
    (None, []) when none does."""
    wanted = norm(text)
    if not wanted:
        return None, []
    for record in records:
        if str(record.get("id", "")) == str(text).strip():
            return record, []
    for matches in (lambda n: n == wanted, lambda n: n.startswith(wanted), lambda n: wanted in n):
        found = [r for r in records if matches(norm(r.get("name", "")))]
        if len(found) == 1:
            return found[0], []
        if found:
            return None, found
    return None, []


def names(records: Sequence[Mapping[str, Any]], limit: int = 8) -> str:
    shown = [str(r.get("name", r.get("id", ""))) for r in records[:limit]]
    return ", ".join(shown) + (f" (+{len(records) - limit} more)" if len(records) > limit else "")


def portal_projects(ctx: Context, portal: str):
    return ctx.data.get("projects", portal=portal)


def portal_of(ctx: Context) -> str:
    """The portal for a portal-wide call (users, tags, project list): `--project PORTAL:...` or the config."""
    if ctx.project and ":" in ctx.project:
        return ctx.project.split(":", 1)[0]
    if ctx.settings.portal_id:
        return ctx.settings.portal_id
    raise ConfigError("config.portal_missing", "No portal to use.",
                      hint=f"Pass --project PORTAL:PROJECT or set portal_id in {ctx.settings.config_path}.")


def scope(ctx: Context) -> tuple[str, str]:
    """(portal, project) for this call. `--project` may be an id, `PORTAL:ID`, or a project name, which
    is looked up in the (cached) project list of the portal."""
    portal, project = ctx.resolve_project()
    if project.isdigit():
        return portal, project
    record, candidates = pick(portal_projects(ctx, portal), project)
    if record is None:
        raise project_not_found(project, candidates)
    return portal, str(record["id"])


def project_not_found(text: str, candidates: Sequence[Mapping[str, Any]]) -> ZohoAxiError:
    if candidates:
        return UsageError("usage.ambiguous_project", f"project {text!r} matches several projects",
                          hint=f"Use the id of one of: {names(candidates)}.")
    return NotFoundError("not_found", f"project {text!r}: not found",
                         hint="Run `zoho-axi project list` to see the project names and ids.")


def resolve_ref(ctx: Context, kind: str, text: str, portal: str, project: str) -> str:
    """Id of the tasklist / milestone `text` names. Warm index first (no request); a bare number is
    taken as an id; otherwise the list is fetched once (which also fills the index) and searched."""
    index = ctx.data.index(portal=portal, project=project)
    found = index.resolve(kind, text)
    if found:
        return found
    if text.strip().isdigit():
        return text.strip()
    listing = ctx.data.get(_RESOURCE[kind], portal=portal, project=project)
    record, candidates = pick(listing, text)
    if record is not None:
        return str(record["id"])
    if candidates:
        raise UsageError(f"usage.ambiguous_{kind}", f"{kind} {text!r} matches several {_RESOURCE[kind]}",
                         hint=f"Use the id of one of: {names(candidates)}.")
    raise NotFoundError("not_found", f"{kind} {text!r}: not found",
                        hint=f"Known {_RESOURCE[kind]}: {names(listing) or 'none'}.")


def optional(warnings: list[str], label: str, fetch: Callable[[], Any]) -> Any:
    """Run a sub-fetch that the command can do without: a Zoho-side failure becomes a warning and None.
    Rate limiting and a rejected token still stop the command, since every later call would fail too."""
    try:
        return fetch()
    except RateLimitedError:
        raise
    except AuthError as exc:
        if exc.code != "auth.forbidden":
            raise
        warnings.append(f"{label} unavailable: {exc.message}")
    except ZohoAxiError as exc:
        warnings.append(f"{label} unavailable: {exc.message}")
    return None
