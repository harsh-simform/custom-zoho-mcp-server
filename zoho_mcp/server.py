import functools

from mcp.server.mcpserver import Image, MCPServer
from .state import STATE, Stage
from .auth import ZohoAuth, ZohoAuthError
from .client import ZohoClient
from . import context_builders as cb
from .logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)
tool_logger = get_logger("zoho_mcp.tools")

_REDACT_PARAMS = {"client_secret", "refresh_token", "access_token"}

mcp = MCPServer("custom-zoho-project-mcp-server")


def logged_tool(func):
    """Logs entry/exit/exceptions for a tool call, redacting secret params by name."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        safe_kwargs = {k: ("***" if k in _REDACT_PARAMS else v) for k, v in kwargs.items()}
        tool_logger.info("-> %s(%s)", func.__name__, safe_kwargs)
        try:
            result = func(*args, **kwargs)
        except Exception:
            tool_logger.exception("x  %s raised", func.__name__)
            raise
        tool_logger.info("<- %s ok", func.__name__)
        return result

    return wrapper


@mcp.tool()
@logged_tool
def configure_oauth(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Configure and validate Zoho OAuth credentials.
    Must succeed before any other tool becomes usable."""
    auth = ZohoAuth(client_id, client_secret, refresh_token)
    try:
        auth.validate()
    except ZohoAuthError as e:
        return f"OAuth configuration failed: {e}"
    STATE.set_auth(auth)
    return "OAuth configured successfully. Next: call list_portals, then select_portal_and_project."


@mcp.tool()
@logged_tool
def list_portals() -> dict:
    """List Zoho portals available to this account. Requires configure_oauth first."""
    STATE.require(Stage.OAUTH_READY)
    return ZohoClient(STATE.auth).list_portals()


@mcp.tool()
@logged_tool
def list_projects(portal_id: str) -> dict:
    """List all projects in a portal, so a project_id can be picked for
    select_portal_and_project. Requires configure_oauth first."""
    STATE.require(Stage.OAUTH_READY)
    return ZohoClient(STATE.auth).list_projects(portal_id)


@mcp.tool()
@logged_tool
def select_portal_and_project(portal_id: str, project_id: str) -> str:
    """Validate and select a portal/project, making it the active one. Multiple
    portal/project pairs can be selected this way without losing earlier ones — use
    switch_portal to move between already-selected pairs, list_selected_portals to see
    them all. Requires configure_oauth first."""
    STATE.require(Stage.OAUTH_READY)
    client = ZohoClient(STATE.auth)
    try:
        client.get_project(portal_id, project_id)
    except Exception as e:
        return f"Could not validate portal/project: {e}"
    STATE.add_selection(portal_id, project_id)
    return f"Portal {portal_id} / Project {project_id} selected and active. You can now fetch task/bug/milestone/project context."


@mcp.tool()
@logged_tool
def switch_portal(portal_id: str, project_id: str) -> str:
    """Switch the active portal/project to one already selected via
    select_portal_and_project — no re-validation call. Fails if that pair hasn't been
    selected yet."""
    try:
        STATE.switch_selection(portal_id, project_id)
    except KeyError as e:
        return str(e)
    return f"Active portal/project switched to {portal_id} / {project_id}."


@mcp.tool()
@logged_tool
def list_selected_portals() -> dict:
    """List every portal/project pair currently selected, and which one is active."""
    return {
        "active": {"portal_id": STATE.portal_id, "project_id": STATE.project_id} if STATE.active_key else None,
        "selections": list(STATE.selections.values()),
    }


@mcp.tool()
@logged_tool
def remove_portal_selection(portal_id: str, project_id: str) -> str:
    """Remove one portal/project pair from the selected set. If it was active, another
    remaining selection (if any) becomes active."""
    STATE.remove_selection(portal_id, project_id)
    return f"Removed {portal_id} / {project_id}. Active is now: {STATE.portal_id}/{STATE.project_id}" \
        if STATE.active_key else f"Removed {portal_id} / {project_id}. No selections remain."


@mcp.tool()
@logged_tool
def reset_selection() -> str:
    """Clear every portal/project selection without losing OAuth config."""
    STATE.reset_selection()
    return "All portal/project selections cleared. Call select_portal_and_project to choose again."


@mcp.tool()
@logged_tool
def get_project_context() -> dict:
    """Full overview of the selected project: details, tasklists, milestones, users, tags.
    Use this for broad questions like 'what's the state of this project'."""
    STATE.require(Stage.CONTEXT_SELECTED)
    client = ZohoClient(STATE.auth)
    return cb.build_project_context(client, STATE.portal_id, STATE.project_id)


@mcp.tool()
@logged_tool
def get_tasklist_context(tasklist_id: str) -> dict:
    """Full context for a tasklist: its metadata plus every task in it."""
    STATE.require(Stage.CONTEXT_SELECTED)
    client = ZohoClient(STATE.auth)
    return cb.build_tasklist_context(client, STATE.portal_id, STATE.project_id, tasklist_id)


@mcp.tool()
@logged_tool
def get_milestone_context(milestone_id: str) -> dict:
    """Full context for a milestone: its metadata plus every task under it."""
    STATE.require(Stage.CONTEXT_SELECTED)
    client = ZohoClient(STATE.auth)
    return cb.build_milestone_context(client, STATE.portal_id, STATE.project_id, milestone_id)


@mcp.tool()
@logged_tool
def get_task_context(task_id: str) -> list[dict | Image]:
    """Full context for a task in one call: details, comments, subtasks, status history,
    and attachments — with any image attachments (e.g. screenshots) downloaded and
    returned inline so they can be visually inspected alongside the text, no follow-up
    call needed."""
    STATE.require(Stage.CONTEXT_SELECTED)
    client = ZohoClient(STATE.auth)
    context = cb.build_task_context(client, STATE.portal_id, STATE.project_id, task_id)
    images = cb.collect_attachment_images(client, context.get("attachments"), context["errors"])
    return [context, *images]


@mcp.tool()
@logged_tool
def get_bug_context(bug_id: str) -> list[dict | Image]:
    """Full context for a bug in one call: details, comments, attachments, and the linked
    task if any — with any image attachments (e.g. screenshots) downloaded and returned
    inline so they can be visually inspected alongside the text, no follow-up call needed."""
    STATE.require(Stage.CONTEXT_SELECTED)
    client = ZohoClient(STATE.auth)
    context = cb.build_bug_context(client, STATE.portal_id, STATE.project_id, bug_id)
    images = cb.collect_attachment_images(client, context.get("attachments"), context["errors"])
    return [context, *images]


@mcp.tool()
@logged_tool
def list_users() -> dict:
    """List users in the current portal — useful for resolving owner names."""
    STATE.require(Stage.OAUTH_READY)
    return ZohoClient(STATE.auth).list_users(STATE.portal_id)


@mcp.tool()
@logged_tool
def list_tags() -> dict:
    """List tags in the current portal — useful for resolving tag IDs on tasks/tasklists."""
    STATE.require(Stage.OAUTH_READY)
    return ZohoClient(STATE.auth).list_tags(STATE.portal_id)


def main() -> None:
    logger.info("zoho-projects MCP server starting (stage=%s)", STATE.stage.value)
    try:
        mcp.run()
    finally:
        logger.info("zoho-projects MCP server stopped")


if __name__ == "__main__":
    main()
