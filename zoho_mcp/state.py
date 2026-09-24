from enum import Enum
from .config import (
    ZOHO_CLIENT_ID,
    ZOHO_CLIENT_SECRET,
    ZOHO_REFRESH_TOKEN,
    ZOHO_PORTAL_ID,
    ZOHO_PROJECT_ID,
)
from .auth import ZohoAuth
from .logging_config import get_logger

logger = get_logger(__name__)

STAGE_ORDER = ["NOT_CONFIGURED", "OAUTH_READY", "CONTEXT_SELECTED"]


class Stage(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    OAUTH_READY = "OAUTH_READY"
    CONTEXT_SELECTED = "CONTEXT_SELECTED"


def _key(portal_id: str, project_id: str) -> str:
    return f"{portal_id}:{project_id}"


class SessionState:
    """Holds OAuth + a set of validated portal/project selections, for the lifetime of
    this process only — nothing here is written to disk. OAuth and the default
    portal/project come from .env at startup (see config.py); configure_oauth and
    select_portal_and_project/switch_portal are in-memory overrides for the running
    session and don't survive a restart. Multiple portals can be selected at once
    (e.g. two orgs); one is "active" at a time and used by every context tool unless a
    tool call targets another already-selected pair explicitly."""

    def __init__(self):
        self.stage: Stage = Stage.NOT_CONFIGURED
        self.auth: ZohoAuth | None = None
        self.selections: dict[str, dict] = {}
        self.active_key: str | None = None
        self._load_auth_from_env()
        self._load_selection_from_env()
        self._reconcile_stage()

    def _load_auth_from_env(self):
        """Secrets live in .env (see config.py). This is the primary way OAuth gets
        configured — configure_oauth remains as a manual, in-memory-only override for
        a single session."""
        if ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET and ZOHO_REFRESH_TOKEN:
            self.auth = ZohoAuth(ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN)
            logger.info("Loaded OAuth credentials from environment (.env)")

    def _load_selection_from_env(self):
        """Default portal/project also comes from .env, not a hardcoded value — makes
        this the active selection at startup so select_portal_and_project doesn't need
        to be called manually every session."""
        if ZOHO_PORTAL_ID and ZOHO_PROJECT_ID:
            key = _key(ZOHO_PORTAL_ID, ZOHO_PROJECT_ID)
            self.selections[key] = {"portal_id": ZOHO_PORTAL_ID, "project_id": ZOHO_PROJECT_ID}
            self.active_key = key
            logger.info("Loaded default portal/project selection from environment (.env)")

    def _reconcile_stage(self):
        if self.auth and self.active_key:
            self.stage = Stage.CONTEXT_SELECTED
        elif self.auth:
            self.stage = Stage.OAUTH_READY
        else:
            self.stage = Stage.NOT_CONFIGURED

    @property
    def portal_id(self) -> str | None:
        sel = self.selections.get(self.active_key) if self.active_key else None
        return sel["portal_id"] if sel else None

    @property
    def project_id(self) -> str | None:
        sel = self.selections.get(self.active_key) if self.active_key else None
        return sel["project_id"] if sel else None

    def require(self, minimum: Stage):
        if STAGE_ORDER.index(self.stage.value) < STAGE_ORDER.index(minimum.value):
            messages = {
                Stage.OAUTH_READY: "OAuth isn't configured yet. Call configure_oauth first.",
                Stage.CONTEXT_SELECTED: "No portal/project selected yet. Call select_portal_and_project first.",
            }
            logger.warning("Blocked tool call: stage=%s, required=%s", self.stage.value, minimum.value)
            raise RuntimeError(messages[minimum])

    def set_auth(self, auth: ZohoAuth):
        """Re-configuring OAuth (e.g. rotating a refresh token) must not drop an
        already-valid portal/project selection back to OAUTH_READY."""
        self.auth = auth
        self.stage = Stage.CONTEXT_SELECTED if self.active_key else Stage.OAUTH_READY
        logger.info("Stage -> %s", self.stage.value)

    def add_selection(self, portal_id: str, project_id: str):
        """Adds (or re-validates) a portal/project pair and makes it active. Prior
        selections are kept — call list_selections() to see them, switch_selection()
        to move between them without re-validating."""
        key = _key(portal_id, project_id)
        self.selections[key] = {"portal_id": portal_id, "project_id": project_id}
        self.active_key = key
        self.stage = Stage.CONTEXT_SELECTED
        logger.info("Stage -> CONTEXT_SELECTED (active=%s), %d selection(s) total", key, len(self.selections))

    def switch_selection(self, portal_id: str, project_id: str):
        key = _key(portal_id, project_id)
        if key not in self.selections:
            raise KeyError(
                f"Portal {portal_id} / project {project_id} hasn't been selected yet. "
                "Call select_portal_and_project first."
            )
        self.active_key = key
        self.stage = Stage.CONTEXT_SELECTED
        logger.info("Active selection switched to %s", key)

    def remove_selection(self, portal_id: str, project_id: str):
        key = _key(portal_id, project_id)
        self.selections.pop(key, None)
        if self.active_key == key:
            self.active_key = next(iter(self.selections), None)
            self.stage = Stage.CONTEXT_SELECTED if self.active_key else Stage.OAUTH_READY
        logger.info("Removed selection %s, %d remaining", key, len(self.selections))

    def reset_selection(self):
        """Clears every portal/project selection without losing OAuth config."""
        self.selections = {}
        self.active_key = None
        self.stage = Stage.OAUTH_READY
        logger.info("All selections cleared, stage=%s", self.stage.value)

    def reset_all(self):
        self.auth = None
        self.selections = {}
        self.active_key = None
        self.stage = Stage.NOT_CONFIGURED
        logger.info("State fully reset")


STATE = SessionState()
