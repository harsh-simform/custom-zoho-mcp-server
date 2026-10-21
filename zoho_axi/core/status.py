"""`auth status` and `auth login-help` as plain functions returning data/text. The CLI (T06) and
the render layer (T05) present them; nothing here prints, and no secret value is ever included."""
from __future__ import annotations

from .auth import ZohoAuth
from .config import Settings
from .oauth_login import DEFAULT_PORT, SCOPES
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
        "Sign in with your browser: register a client once, then run `zoho-axi auth login`.",
        f"1. Open https://api-console.zoho.{settings.dc} and add a client of type Server-based Applications.",
        f"2. Set its Authorized Redirect URI to http://localhost:{DEFAULT_PORT}/callback (or your --port).",
        f"3. Write its client_id and client_secret (and dc, if not com) to {settings.config_path}",
        f"   and run `chmod 600 {settings.config_path}`; or set ZOHO_AXI_CLIENT_ID / ZOHO_AXI_CLIENT_SECRET.",
        "4. Run `zoho-axi auth login`: it opens Zoho's consent page and saves the refresh token for you.",
        "   Read-only scopes are requested: " + ", ".join(SCOPES) + ".",
        "No browser (ssh, CI)? Use a Self Client at the same console instead and put its refresh_token in the config.",
        "Environment variables ZOHO_AXI_<KEY> override the file.",
    ])
