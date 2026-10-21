"""`auth status` and `auth login-help` as plain functions returning data/text. The CLI (T06) and
the render layer (T05) present them; nothing here prints, and no secret value is ever included."""
from __future__ import annotations

from .auth import ZohoAuth
from .config import Settings
from .errors import AuthError, UpstreamError, ZohoAxiError

_REPORTED = ("client_id", "client_secret", "refresh_token", "dc", "portal_id", "project_id")


def _source(settings: Settings, name: str) -> str:
    source = settings.sources[name]
    return "unset" if source == "default" and getattr(settings, name) is None else source


def auth_status(settings: Settings, auth: ZohoAuth | None = None, online: bool = True) -> dict:
    missing = [
        k for k in ("client_id", "client_secret", "refresh_token") if not getattr(settings, k)
    ]
    result: dict = {
        "config_file": str(settings.config_path),
        "config_file_exists": settings.config_path.is_file(),
        "settings": {name: _source(settings, name) for name in _REPORTED},
        "credentials": f"missing: {', '.join(missing)}" if missing else "complete",
        "token": "unverified",
        "warnings": list(settings.warnings),
    }
    if missing or not online:
        return result

    try:
        (auth or ZohoAuth(settings)).validate()
        result["token"] = "valid"
    except AuthError as exc:
        result["token"], result["token_error"] = "invalid", exc.code
    except UpstreamError as exc:
        result["token"], result["token_error"] = "unreachable", exc.code
    except ZohoAxiError as exc:
        result["token"], result["token_error"] = "unverified", exc.code
    except Exception as exc:  # a diagnostic command must report broken setups, not crash on them
        result["token"], result["token_error"] = "unverified", type(exc).__name__
    return result


def login_help(settings: Settings) -> str:
    return "\n".join([
        "Zoho has no interactive login here; create a Self Client and put its values in the config file.",
        f"1. Open https://api-console.zoho.{settings.dc} and create a Self Client.",
        "2. Request read scopes: ZohoProjects.<module>.READ for portals, projects, tasks, tasklists,",
        "   milestones, bugs, users and tags, plus ZohoProjects.search.READ (needed to resolve keys",
        "   like PROJ1-T1000 to ids).",
        "3. Generate a refresh token from the Self Client's generated code.",
        f"4. Write client_id, client_secret and refresh_token (and dc, portal_id, project_id) to {settings.config_path}",
        f"   and run `chmod 600 {settings.config_path}`.",
        "Environment variables ZOHO_AXI_<KEY> override the file.",
    ])
