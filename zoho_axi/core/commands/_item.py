"""Shared plumbing for the item commands: `task` (T08) and `bug` (T09), later `resolve` (T11).

An *item* is a task or a bug addressed by a human key (`PROJ1-T1000`) or a numeric id. This module owns
the parts that are the same for both:

* `ItemKind`        which gateway resources, id parameter and Search module a kind uses (`TASK`, `BUG`)
* `resolve_item`    key -> id: numeric ids pass through, keys come from the derived index (no request) or the
                    legacy Search endpoint, and a hit is remembered so the next lookup is free
* `fetch_record`    the item's detail record
* `fetch_slice`     a sub-resource (comments, attachments, ...) whose failure is a warning, not an error
* `comment_rows` / `attachment_rows` / `is_image` / `human_bytes`   display rows for the render schemas

Everything reads Zoho through `ctx.data` (the cache in front of the gateway), so a warm call costs no
request. No formatting happens here: the renderer owns text, truncation and TOON.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..attachments import IMAGE_EXTENSIONS, default_attachments_dir, human_bytes, is_image, item_dir, save_images
from ..errors import NotFoundError
from ._scope import optional
from ._shape import norm_date, person_name, task_key
from .base import Context

__all__ = ["IMAGE_EXTENSIONS", "human_bytes", "is_image"]  # re-exported: they lived here before T12


@dataclass(frozen=True)
class ItemKind:
    name: str            # "task": also the gateway id parameter (`task=...`) and the key-memo kind
    resource: str        # gateway resource of the detail record
    comments: str        # gateway resource of the comments
    attachments: str     # gateway resource of the attachments
    search_module: str   # `module=` of the Search endpoint used to resolve a key
    key_fields: tuple[str, ...] = ("key", "prefix")  # spellings of the human key in a search hit
    id_fields: tuple[str, ...] = ("id", "entity_id")  # spellings of the id in a search hit


TASK = ItemKind("task", "task", "task_comments", "task_attachments", "tasks")
# Bugs: the legacy Search endpoint is documented to answer `{"issue": [{issue_id, issue_id_str, prefix, ...}]}`
# while the legacy code reads `{"bugs": [{id, key}]}`; which is real is unverified, so both spellings are read.
BUG = ItemKind("bug", "bug", "bug_comments", "bug_attachments", "bugs",
               id_fields=("id", "entity_id", "issue_id_str", "issue_id"))


@dataclass(frozen=True)
class Item:
    kind: ItemKind
    portal: str
    project: str
    id: str

    @property
    def ids(self) -> dict[str, Any]:
        """The keyword ids the gateway wants for this item's own resources."""
        return {"portal": self.portal, "project": self.project, self.kind.name: self.id}


def _first(record: Mapping[str, Any], names: Sequence[str]) -> str:
    return next((str(record[n]).strip() for n in names if record.get(n) not in (None, "")), "")


def resolve_item(ctx: Context, kind: ItemKind, ref: str, portal: str, project: str) -> Item:
    """The item `ref` names. Numeric -> as is. A key -> the index memo, else one Search call (exact key
    match only: Search is fuzzy) whose hit is remembered. A miss is a not-found error (exit 4)."""
    ref = ref.strip()
    if ref.isdigit():
        return Item(kind, portal, project, ref)
    index = ctx.data.index(portal=portal, project=project)
    known = None if ctx.fresh else index.resolve_key(ref, kind=kind.name)
    if known:
        return Item(kind, portal, project, known)
    hits = ctx.data.get("search", portal=portal, project=project,
                        params={"search_term": ref, "module": kind.search_module})
    for hit in hits:
        ident = _first(hit, kind.id_fields)
        if ident and _first(hit, kind.key_fields).upper() == ref.upper():
            index.remember_key(ref, ident, kind=kind.name)
            return Item(kind, portal, project, ident)
    raise NotFoundError(
        "not_found", f"{kind.name} {ref!r}: not found in project {project}",
        hint=f"Check the key. Search only covers the project in use (pass --project to change it); "
             f"a numeric id works directly: `zoho-axi {kind.name} <id>`.")


def fetch_record(ctx: Context, item: Item) -> dict:
    """The detail record; a failure here is the command's failure (not-found is exit 4)."""
    record = ctx.data.get(item.kind.resource, **item.ids)
    key = task_key(record)
    if key and key != item.id:  # a later lookup by key should not need Search
        ctx.data.index(portal=item.portal, project=item.project).remember_key(key, item.id, kind=item.kind.name)
    return record


def fetch_slice(ctx: Context, warnings: list[str], label: str, resource: str, item: Item,
                params: Mapping[str, str] | None = None) -> Sequence[Mapping[str, Any]] | None:
    """A collection the command can do without. On a Zoho-side failure: a `warnings` line and None."""
    return optional(warnings, label, lambda: ctx.data.get(resource, params=params, **item.ids))


def count_of(page: Sequence[Any] | None) -> int | str | None:
    """Row count of a fetched page: `N`, or `N+` when Zoho says more pages exist; None if it failed."""
    if page is None:
        return None
    return f"{len(page)}+" if getattr(page, "has_more", None) is True else len(page)


# --- display rows ------------------------------------------------------------------------------------

def comment_rows(records: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """{by, at, text} rows, oldest first as Zoho sends them. Author: v3 `created_by` object or legacy
    `added_person`; v3 issue comments use an `added_by` object (a name, never the raw object is shown); text:
    `comment` (the gateway folds legacy `content` into it); at: a date."""
    return [{
        "by": person_name(r.get("created_by")) or person_name(r.get("added_person")) or person_name(r.get("added_by")),
        "at": norm_date(r.get("created_time") or r.get("created_time_long")),
        "text": str(r.get("comment") or ""),
    } for r in records]


def attachment_rows(records: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """{name, type, size} only; never a download URL (images are fetched by `image_rows`, only on request)."""
    return [{
        "name": str(r.get("file_name") or ""),
        "type": str(r.get("content_type") or ""),
        "size": human_bytes(r.get("size")) if r.get("size") not in (None, "") else "",
    } for r in records]


def image_rows(ctx: Context, item: Item, records: Sequence[Mapping[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    """`--images`: download the item's images (at most `max_images`, each at most `max_attachment_bytes`) into
    `<cache>/zoho-axi/attachments/<kind>-<id>/` and return {name, bytes, path} rows. Whatever was skipped or
    refused is added to `warnings`; the command still succeeds. `--fresh` downloads again."""
    base = default_attachments_dir(os.environ if ctx.env is None else ctx.env)
    batch = save_images(ctx.gateway.download, records, dest=item_dir(base, item.kind.name, item.id),
                        max_images=ctx.settings.max_images, max_bytes=ctx.settings.max_attachment_bytes,
                        fresh=ctx.fresh)
    warnings.extend(batch.warnings())
    return [{"name": i.name, "bytes": i.size, "path": str(i.path)} for i in batch.images]
