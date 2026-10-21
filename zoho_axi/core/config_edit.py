"""Write settings into the user's config file without disturbing anything else in it.

`set_default_project` and `set_refresh_token` edit the file as text, never by re-serializing TOML, so comments, ordering,
blank lines, other keys and tables survive byte for byte; only the lines of the keys being set
change (a trailing `# comment` on such a line is kept) or, when a key is missing, are inserted at the
end of the top-level keys. The result is re-parsed and compared with the original before anything is
written, and the write is atomic: a temp file in the same directory (mode 600), fsync, rename. A failure
at any point leaves the original file as it was.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .errors import ConfigError, UsageError

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.~+/=-]{8,512}$")
_TABLE = re.compile(r"^\s*\[")
# key = value [# comment]; the value is a basic string, a literal string or a bare token.
_VALUE = r'("(?:[^"\\]|\\.)*"|\'[^\']*\'|[^#\s]+)'


def _line_for(key: str) -> re.Pattern:
    return re.compile(rf"^(?P<head>\s*{key}\s*=\s*){_VALUE}(?P<tail>\s*(?:#.*)?)$")


def _edit(text: str, values: dict[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    table_at = next((i for i, line in enumerate(lines) if _TABLE.match(line)), len(lines))
    missing: list[str] = []
    for key, value in values.items():
        pattern = _line_for(key)
        for i in range(table_at):
            body = lines[i].rstrip("\r\n")
            m = pattern.match(body)
            if m:
                ending = lines[i][len(body):] or "\n"
                lines[i] = f"{m.group('head')}{json.dumps(value)}{m.group('tail')}{ending}"
                break
        else:
            missing.append(key)
    if missing:
        at = table_at
        while at > 0 and not lines[at - 1].strip():  # keep the blank line that separates the first table
            at -= 1
        if at > 0 and not lines[at - 1].endswith("\n"):
            lines[at - 1] += "\n"
        lines[at:at] = [f"{key} = {json.dumps(values[key])}\n" for key in missing]
    return "".join(lines)


def set_default_project(path: Path, portal_id: str, project_id: str) -> None:
    for name, value in (("portal_id", portal_id), ("project_id", project_id)):
        if not _SAFE_ID.match(value):
            raise UsageError("usage.invalid_arguments", f"{name} {value!r} is not a valid Zoho id",
                             hint="Zoho ids are digits; pass the project's id or its name.")
    wanted = {"portal_id": portal_id, "project_id": project_id}
    _write(path, wanted, f"portal_id = \"{portal_id}\", project_id = \"{project_id}\"")


def set_refresh_token(path: Path, refresh_token: str) -> None:
    if not _SAFE_TOKEN.match(refresh_token):
        raise ConfigError("config.unwritable", "Zoho returned a refresh token in an unexpected format; nothing was saved.")
    # The token is a secret, so the by-hand fallback names the key but never echoes the value.
    _write(path, {"refresh_token": refresh_token}, "refresh_token = \"<the token>\"")


def _write(path: Path, wanted: dict[str, str], by_hand: str) -> None:
    path = Path(os.path.realpath(path))  # write through a symlinked config rather than replacing the link
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = _edit(original, wanted)

    try:
        before, after = tomllib.loads(original), tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("config.invalid", f"{path} is not valid TOML: {exc}", hint=f"Fix the syntax in {path}.") from None
    expected = {**before, **wanted}
    if after != expected:  # an unusual layout we do not understand: refuse rather than corrupt the file
        raise ConfigError("config.unwritable", f"Could not safely update {', '.join(wanted)} in {path}.",
                          hint=f"Edit the file by hand: {by_hand}.")

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(updated)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
