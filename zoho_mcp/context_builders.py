import json

from mcp.server.mcpserver import Image

from .client import ZohoClient
from .config import MAX_ATTACHMENT_BYTES, MAX_CONTEXT_IMAGES
from .logging_config import get_logger

logger = get_logger(__name__)

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _safe(errors, key, fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        logger.warning("Sub-fetch %r failed, continuing with partial context: %s", key, e)
        errors.append(f"{key} failed: {e}")
        return None


def _first(record, *keys):
    """Zoho's field casing varies across API versions/endpoints (FILENAME vs file_name);
    check every known spelling instead of betting on one."""
    for key in keys:
        value = record.get(key) if isinstance(record, dict) else None
        if value:
            return value
    return None


def _attachment_records(payload):
    """Normalizes attachment list responses, whose shape varies (bare list, or a dict
    wrapping the list under 'attachments'/'attachment'/'data')."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("attachments", "attachment", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _is_image_attachment(record):
    content_type = (_first(record, "content_type", "CONTENT_TYPE", "mime_type", "type") or "").lower()
    if content_type.startswith("image/"):
        return True
    filename = (_first(record, "file_name", "FILENAME", "filename", "name") or "").lower()
    return filename.endswith(_IMAGE_EXTENSIONS)


def collect_attachment_images(client: ZohoClient, attachments_payload, errors,
                               max_images=MAX_CONTEXT_IMAGES, max_bytes=MAX_ATTACHMENT_BYTES):
    """Downloads image attachments (screenshots, etc.) so they come back as actual images
    in the same tool call instead of just filenames — the point being one call gives
    Claude everything, text and visual, needed to investigate a task/bug without a
    follow-up round trip. Appends to `errors` (shared with the rest of the context)
    rather than raising, per this module's partial-failure philosophy."""
    images = []
    for record in _attachment_records(attachments_payload):
        if len(images) >= max_images:
            break
        if not _is_image_attachment(record):
            continue
        name = _first(record, "file_name", "FILENAME", "filename", "name") or "attachment"
        url = _first(record, "download_url", "DOWNLOAD_URL", "downloadUrl", "url", "link", "file_url")
        if not url:
            errors.append(f"image attachment '{name}' has no download URL, skipped")
            continue
        try:
            data, content_type = client.download_file(url, max_bytes=max_bytes)
            fmt = content_type.split("/")[-1].split(";")[0] if content_type else None
            images.append(Image(data=data, format=fmt or None))
        except Exception as e:
            logger.warning("Downloading image attachment %r failed: %s", name, e)
            errors.append(f"downloading image attachment '{name}' failed: {e}")
    return images


def _resolve_id(client: ZohoClient, portal_id, project_id, id_or_key, module, errors):
    """Zoho's task/bug detail endpoints only accept the internal numeric ID, but the
    human-readable display key (e.g. 'AD1-T1153') is what people actually reference —
    the point of this server is one call, no separate lookup step. If id_or_key is
    already numeric, use it as-is; otherwise resolve it via the Search API first."""
    if str(id_or_key).isdigit():
        return id_or_key
    try:
        result = client.search(portal_id, project_id, id_or_key, module)
        candidates = result.get(module, []) if isinstance(result, dict) else []
        for item in candidates:
            if str(item.get("key", "")).lower() == str(id_or_key).lower():
                return item["id"]
        if candidates:
            return candidates[0]["id"]
        errors.append(f"could not resolve '{id_or_key}' to a {module[:-1]} ID: no search match")
    except Exception as e:
        errors.append(f"resolving '{id_or_key}' via search failed: {e}")
    return id_or_key


def build_project_context(client: ZohoClient, portal_id, project_id):
    errors = []
    return {
        "project": _safe(errors, "project", client.get_project, portal_id, project_id),
        "tasklists": _safe(errors, "tasklists", client.get_project_tasklists, portal_id, project_id),
        "milestones": _safe(errors, "milestones", client.list_milestones, portal_id, project_id),
        "users": _safe(errors, "users", client.list_users, portal_id),
        "tags": _safe(errors, "tags", client.list_tags, portal_id),
        "errors": errors,
    }


def build_tasklist_context(client: ZohoClient, portal_id, project_id, tasklist_id):
    errors = []
    filter_json = json.dumps({
        "criteria": [{"field_name": "tasklist_id", "criteria_condition": "is", "value": [str(tasklist_id)]}],
        "pattern": "1",
    })
    return {
        "tasklist": _safe(errors, "tasklist", client.get_tasklist, portal_id, project_id, tasklist_id),
        "tasks": _safe(errors, "tasks", client.get_tasks, portal_id, project_id, filter_json),
        "errors": errors,
    }


def build_milestone_context(client: ZohoClient, portal_id, project_id, milestone_id):
    errors = []
    filter_json = json.dumps({
        "criteria": [{"field_name": "milestone_id", "criteria_condition": "is", "value": [str(milestone_id)]}],
        "pattern": "1",
    })
    return {
        "milestone": _safe(errors, "milestone", client.get_milestone, portal_id, project_id, milestone_id),
        "tasks": _safe(errors, "tasks", client.get_tasks, portal_id, project_id, filter_json),
        "errors": errors,
    }


def _task_status_history(client, portal_id, project_id, task_id, errors):
    """Zoho's taskstatushistory endpoint ignores every task-scoping param we've found
    (task_id, task_ids, and filter criteria on task_id/id all silently return the same
    unfiltered project-wide page) — confirmed by testing, not assumed. Filtering
    client-side instead of returning ~100 unrelated tasks' histories, which was both
    wrong and what blew responses past the token limit."""
    raw = _safe(errors, "status_history", client.get_task_status_history, portal_id, project_id, task_id)
    if raw is None:
        return None
    matches = [entry for entry in raw if str(entry.get("id")) == str(task_id)]
    if not matches:
        errors.append(
            f"status_history: task {task_id} wasn't on the fetched page (Zoho's "
            "endpoint doesn't support server-side task filtering) — omitted rather than "
            "returning unrelated tasks' histories"
        )
        return []
    return matches


def _task_subtasks(client, portal_id, project_id, task, errors):
    """Zoho's v3 tasks endpoint doesn't honor any subtask-scoping param we've found
    (has_parents filter 400s outright; parent_id and a parent_id filter both silently
    return the unfiltered project task list instead) — confirmed by testing. The task
    detail's own association_info.has_subtasks is reliable, so: skip the call entirely
    when it's false (the common case, and avoids a guaranteed-broken round trip), and
    when true, still attempt parent_id (best effort) but flag the result as unverified
    rather than presenting it as a confirmed subtask list."""
    if task is None or task.get("association_info", {}).get("has_subtasks") is False:
        return []
    task_id = task["id"]
    result = _safe(errors, "subtasks", client.get_tasks, portal_id, project_id, None, {"parent_id": task_id})
    if result is not None:
        errors.append(
            "subtasks: Zoho's API doesn't confirm-filter by parent_id — this list may include "
            "unrelated project tasks rather than only this task's subtasks"
        )
    return result


def build_task_context(client: ZohoClient, portal_id, project_id, task_id):
    errors = []
    task_id = _resolve_id(client, portal_id, project_id, task_id, "tasks", errors)
    task = _safe(errors, "task", client.get_task, portal_id, project_id, task_id)
    return {
        "task": task,
        "comments": _safe(errors, "comments", client.get_task_comments, portal_id, project_id, task_id),
        "subtasks": _task_subtasks(client, portal_id, project_id, task, errors),
        "status_history": _task_status_history(client, portal_id, project_id, task_id, errors),
        "attachments": _safe(errors, "attachments", client.get_task_attachments, portal_id, project_id, task_id),
        "errors": errors,
    }


def build_bug_context(client: ZohoClient, portal_id, project_id, bug_id):
    errors = []
    bug_id = _resolve_id(client, portal_id, project_id, bug_id, "bugs", errors)
    bug = _safe(errors, "bug", client.get_bug, portal_id, project_id, bug_id)
    associated_task = None
    if bug and bug.get("associated_task", {}).get("id"):
        associated_task = _safe(
            errors, "associated_task", client.get_task, portal_id, project_id, bug["associated_task"]["id"]
        )
    return {
        "bug": bug,
        "comments": _safe(errors, "comments", client.get_bug_comments, portal_id, project_id, bug_id),
        "attachments": _safe(errors, "attachments", client.get_bug_attachments, portal_id, project_id, bug_id),
        "associated_task": associated_task,
        "errors": errors,
    }
