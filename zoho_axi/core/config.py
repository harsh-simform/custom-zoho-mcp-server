"""Settings, built once at startup and passed down. Importing this module has no side effects:
nothing reads the environment or the filesystem until `load_settings` is called.

Precedence, highest first: explicit overrides (CLI flags) > ZOHO_AXI_* env > config file >
legacy ZOHO_* env > legacy repo `.env` > defaults. The two legacy sources are deprecated
fallbacks; using either yields a single warning.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from dotenv import dotenv_values

from .errors import AuthError, ConfigError

DEFAULT_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

# setting name -> (type, default)
_SPEC: dict[str, tuple[type, object]] = {
    "client_id": (str, None),
    "client_secret": (str, None),
    "refresh_token": (str, None),
    "dc": (str, "com"),
    "portal_id": (str, None),
    "project_id": (str, None),
    "max_images": (int, 5),
    "max_attachment_bytes": (int, DEFAULT_MAX_ATTACHMENT_BYTES),
    "ttl_long_seconds": (int, 24 * 3600),
    "ttl_task_seconds": (int, 300),
    "ttl_comment_seconds": (int, 120),
    "log_level": (str, "INFO"),
    "log_file": (str, None),
}

_LEGACY_ENV = {
    "ZOHO_CLIENT_ID": "client_id",
    "ZOHO_CLIENT_SECRET": "client_secret",
    "ZOHO_REFRESH_TOKEN": "refresh_token",
    "ZOHO_PORTAL_ID": "portal_id",
    "ZOHO_PROJECT_ID": "project_id",
    "ZOHO_DC": "dc",
    "ZOHO_MCP_MAX_CONTEXT_IMAGES": "max_images",
    "ZOHO_MCP_MAX_ATTACHMENT_BYTES": "max_attachment_bytes",
    "ZOHO_MCP_LOG_LEVEL": "log_level",
    "ZOHO_MCP_LOG_FILE": "log_file",
}

_LEGACY_SOURCES = ("legacy-env", "legacy-dotenv")

# The data center is interpolated into hostnames that receive the OAuth credentials, so it is an allowlist.
VALID_DATA_CENTERS = ("com", "eu", "in", "com.au", "jp")

_MINIMUMS = {
    "max_images": 0,
    "max_attachment_bytes": 1,
    "ttl_long_seconds": 0,
    "ttl_task_seconds": 0,
    "ttl_comment_seconds": 0,
}

# Only meaningful in a source checkout (see _default_legacy_env_file); computing it reads nothing.
_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    client_id: str | None = field(repr=False)
    client_secret: str | None = field(repr=False)
    refresh_token: str | None = field(repr=False)
    dc: str
    portal_id: str | None
    project_id: str | None
    max_images: int
    max_attachment_bytes: int
    ttl_long_seconds: int
    ttl_task_seconds: int
    ttl_comment_seconds: int
    log_level: str
    log_file: str | None
    config_path: Path
    sources: Mapping[str, str] = field(repr=False)
    warnings: list[str] = field(repr=False)

    def require_credentials(self) -> tuple[str, str, str]:
        creds = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }
        missing = [k for k, v in creds.items() if not v]
        if missing:
            raise AuthError(
                "auth.missing",
                "Zoho OAuth credentials are not configured.",
                hint=(
                    f"missing: {', '.join(missing)}. Add them to {self.config_path} (mode 600) "
                    "or set the matching ZOHO_AXI_<KEY> environment variables."
                ),
            )
        return self.client_id, self.client_secret, self.refresh_token  # type: ignore[return-value]

    @property
    def accounts_base(self) -> str:
        return f"https://accounts.zoho.{self.dc}"

    @property
    def api_v3_base(self) -> str:
        return f"https://projectsapi.zoho.{self.dc}/api/v3"

    @property
    def restapi_base(self) -> str:
        return f"https://projectsapi.zoho.{self.dc}/restapi"


def default_config_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path(env.get("HOME") or Path.home()) / ".config"
    return root / "zoho-axi" / "config.toml"


def _default_legacy_env_file(env: Mapping[str, str]) -> Path | None:
    explicit = env.get("ZOHO_MCP_ENV_FILE")
    if explicit:
        return Path(explicit)
    # Only a source checkout has a repo-root .env; for an installed package this path is arbitrary.
    return _REPO_ROOT / ".env" if (_REPO_ROOT / "pyproject.toml").is_file() else None


def _invalid(name: str, source: str, problem: str, hint: str | None = None) -> ConfigError:
    return ConfigError(
        "config.invalid",
        f"{name} from {source} {problem}",
        hint=hint or f"Fix `{name}` in your config file or environment.",
    )


def _coerce(name: str, raw: object, source: str):
    kind = _SPEC[name][0]
    if kind is int:
        if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
            raise _invalid(name, source, f"must be a whole number, got {raw!r}")
        try:
            value = int(raw)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            raise _invalid(name, source, f"must be a whole number, got {raw!r}") from None
        if value < _MINIMUMS.get(name, 0):
            raise _invalid(name, source, f"must be at least {_MINIMUMS.get(name, 0)}, got {value}")
        return value
    value = str(raw)
    if name == "dc" and value not in VALID_DATA_CENTERS:
        raise _invalid(
            name, source, f"must be one of {', '.join(VALID_DATA_CENTERS)}, got {value!r}",
            hint=f"Set dc to your Zoho data center: {', '.join(VALID_DATA_CENTERS)}.",
        )
    return value


def _read_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("config.invalid", f"{path} is not valid TOML: {exc}", hint=f"Fix the syntax in {path}.") from None


def _loose_permissions(path: Path) -> bool:
    return os.name != "nt" and path.is_file() and bool(stat.S_IMODE(path.stat().st_mode) & 0o077)


def split_project_ref(ref: str) -> tuple[str | None, str]:
    """`PORTAL:PROJECT` -> (portal, project); anything else -> (None, ref). A colon only separates a portal
    when what is left of it is all digits (a portal id), so a project NAME like "Demo: Site" stays a name."""
    head, sep, tail = ref.partition(":")
    if sep and head.isdigit():
        return head, tail
    return None, ref


def resolve_project(settings: Settings, project_arg: str | None = None) -> tuple[str, str]:
    """`--project` (either `PROJECT` or `PORTAL:PROJECT`) wins over the configured default.
    There is no stateful "selected project": every call resolves it afresh."""
    portal, project = settings.portal_id, settings.project_id
    if project_arg:
        arg_portal, project = split_project_ref(project_arg)
        portal = arg_portal or portal
    if not portal or not project:
        raise ConfigError(
            "config.project_missing",
            "No portal/project to use.",
            hint=(
                "Pass --project PROJECT (or PORTAL:PROJECT), or set portal_id and project_id "
                f"in {settings.config_path}."
            ),
        )
    return portal, project


def load_settings(
    env: Mapping[str, str] | None = None,
    config_path: Path | None = None,
    legacy_env_file: Path | None = None,
    overrides: Mapping[str, object] | None = None,
    strict: bool = False,
) -> Settings:
    env = os.environ if env is None else env
    path = Path(config_path) if config_path else default_config_path(env)
    legacy_dotenv_path = Path(legacy_env_file) if legacy_env_file else _default_legacy_env_file(env)

    permission_warning = None
    if _loose_permissions(path):
        message = f"{path} is readable by other users; run `chmod 600 {path}`."
        if strict:
            raise ConfigError("config.permissions", message, hint=f"chmod 600 {path}")
        permission_warning = message

    file_values = _read_file(path)
    dotenv_legacy = {k: v for k, v in dotenv_values(legacy_dotenv_path).items() if v} if legacy_dotenv_path else {}
    file_values = {k: v for k, v in file_values.items() if v != ""}  # an empty value means "unset", as in env
    env_legacy = {k: env[k] for k in _LEGACY_ENV if env.get(k)}

    layers: list[tuple[str, Mapping[str, object]]] = [
        ("flag", {k: v for k, v in (overrides or {}).items() if v is not None}),
        ("env", {n: env[f"ZOHO_AXI_{n.upper()}"] for n in _SPEC if env.get(f"ZOHO_AXI_{n.upper()}")}),
        ("file", file_values),
        ("legacy-env", {_LEGACY_ENV[k]: v for k, v in env_legacy.items()}),
        ("legacy-dotenv", {_LEGACY_ENV[k]: v for k, v in dotenv_legacy.items() if k in _LEGACY_ENV}),
    ]

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for name, (_, default) in _SPEC.items():
        for source, layer in layers:
            if name in layer:
                values[name] = _coerce(name, layer[name], source)
                sources[name] = source
                break
        else:
            values[name] = default
            sources[name] = "default"

    warnings: list[str] = [permission_warning] if permission_warning else []
    legacy_used = sorted({s for s in sources.values() if s in _LEGACY_SOURCES})
    if legacy_used:
        warnings.append(
            "deprecated config source in use (" + ", ".join(legacy_used) + f"); move settings to {path}"
        )

    return Settings(config_path=path, sources=sources, warnings=warnings, **values)
