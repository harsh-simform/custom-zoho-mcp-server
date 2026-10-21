"""Editing `~/.claude/settings.json` and `~/.claude/skills/` safely (T14). Pure functions plus one atomic
writer; nothing here prints, prompts or reads stdin, and importing it has no side effects.

The hook entry follows Claude Code's documented settings format (checked with Context7 against the
hooks reference, https://code.claude.com/docs/en/hooks, library ids /pjbrito/claude-code-docs and
/anthropics/claude-code): `hooks.<Event>` is a list of groups `{"matcher": ..., "hooks": [handler]}`; a
SessionStart `matcher` is one of `startup`, `resume`, `clear`, `compact` (letters and `|` only means an
exact list); a handler is `{"type": "command", "command": ..., "timeout": <seconds>}`; for SessionStart,
stdout of an exit-0 command is added to Claude's context.

Safety rule: every path this module hands out lies inside the home directory it was given, after
symlinks are resolved (`ensure_inside`). The home is `HOME` from the injected environment; only when that
is unset does it fall back to the OS home. A relative HOME is refused.

The settings file is edited as parsed JSON and re-serialized with the file's own indent and trailing
newline, keeping key order, so unrelated settings are untouched in value and layout. A file whose
layout is not reproducible that way (compact arrays, comments are not JSON at all) is flagged so the
diff noise is not a surprise; an unparseable file is never overwritten.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError, UsageError

HOOK_COMMAND = "zoho-axi session-start"
HOOK_MATCHER = "startup|resume|clear|compact"
HOOK_TIMEOUT_SECONDS = 10  # the command enforces its own, shorter deadline; this is the harness backstop
HOOK_EVENT = "SessionStart"
_OUR_EXECUTABLES = {"zoho-axi", "zoho"}


# --- where things live ---------------------------------------------------------------------------

def home_dir(env: Mapping[str, str]) -> Path:
    raw = env.get("HOME")
    if not raw:
        return Path.home()
    home = Path(raw)
    if not home.is_absolute():
        raise UsageError("usage.unsafe_target", f"HOME is not an absolute path ({raw!r}); refusing to write under it.",
                         hint="Run with an absolute HOME.")
    return home


def ensure_inside(home: Path, path: Path) -> Path:
    """`path` itself, or UsageError if it (with symlinks resolved) is not inside `home` (resolved too)."""
    real_home, real = Path(os.path.realpath(home)), Path(os.path.realpath(path))
    if real != real_home and real_home not in real.parents:
        raise UsageError("usage.unsafe_target", f"{path} resolves to {real}, which is outside the home directory {real_home}.",
                         hint="Nothing was written. Edit that file by hand, or run with a HOME that contains it.")
    return path


def claude_dir(env: Mapping[str, str]) -> Path:
    home = home_dir(env)
    return ensure_inside(home, home / ".claude")


def settings_file(env: Mapping[str, str]) -> Path:
    return ensure_inside(home_dir(env), claude_dir(env) / "settings.json")


def skill_file(env: Mapping[str, str]) -> Path:
    return ensure_inside(home_dir(env), claude_dir(env) / "skills" / "zoho-axi" / "SKILL.md")


# --- settings.json -------------------------------------------------------------------------------

def parse_settings(text: str | None, path: Path) -> dict:
    if text is None or not text.strip():
        return {}
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise ConfigError("config.invalid", f"{path} is not valid JSON: {exc}",
                          hint=f"Nothing was changed. Fix the syntax in {path}, then retry.") from None
    if not isinstance(doc, dict):
        raise ConfigError("config.invalid", f"{path} does not hold a JSON object.", hint=f"Nothing was changed. Fix {path}, then retry.")
    return doc


def _groups(doc: dict, path: Path, create: bool) -> list | None:
    hooks = doc.get("hooks")
    if hooks is None:
        if not create:
            return None
        hooks = doc["hooks"] = {}
    if not isinstance(hooks, dict):
        raise _unwritable(path, '"hooks" is not an object')
    groups = hooks.get(HOOK_EVENT)
    if groups is None:
        if not create:
            return None
        groups = hooks[HOOK_EVENT] = []
    if not isinstance(groups, list):
        raise _unwritable(path, f'"hooks.{HOOK_EVENT}" is not a list')
    return groups


def _unwritable(path: Path, why: str) -> ConfigError:
    return ConfigError("config.unwritable", f"{path}: {why}, so the hook cannot be added or removed safely.",
                       hint=f"Nothing was changed. Edit {path} by hand (see `zoho-axi hooks install` for the entry).")


def _is_ours(handler: Any) -> bool:
    """`zoho-axi session-start` (or `zoho ...`, with or without a directory in front of the executable)."""
    if not isinstance(handler, dict) or not isinstance(handler.get("command"), str):
        return False
    try:
        parts = shlex.split(handler["command"])
    except ValueError:
        return False
    return len(parts) == 2 and os.path.basename(parts[0]) in _OUR_EXECUTABLES and parts[1] == "session-start"


def _our_handlers(groups: list) -> list[tuple[dict, dict]]:
    return [(g, h) for g in groups if isinstance(g, dict) and isinstance(g.get("hooks"), list)
            for h in g["hooks"] if _is_ours(h)]


def is_installed(doc: dict, path: Path) -> bool:
    groups = _groups(doc, path, create=False)
    return bool(groups and _our_handlers(groups))


def add_hook(doc: dict, path: Path) -> bool:
    """Append our SessionStart group unless one is there already. True when `doc` changed."""
    if is_installed(doc, path):
        return False
    groups = _groups(doc, path, create=True)
    assert groups is not None
    groups.append({"matcher": HOOK_MATCHER,
                   "hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": HOOK_TIMEOUT_SECONDS}]})
    return True


def remove_hook(doc: dict, path: Path) -> bool:
    """Remove our handler(s); drop the group, event list and `hooks` object only if that left them empty."""
    if not is_installed(doc, path):
        return False
    groups = _groups(doc, path, create=False)
    assert groups is not None
    touched = []
    for group, handler in _our_handlers(groups):
        group["hooks"].remove(handler)
        touched.append(group)
    groups[:] = [g for g in groups
                 if not (any(g is t for t in touched) and g["hooks"] == [] and set(g) <= {"matcher", "hooks"})]
    if not groups:
        del doc["hooks"][HOOK_EVENT]
        if not doc["hooks"]:
            del doc["hooks"]
    return True


def dump_settings(doc: dict, original: str | None) -> str:
    """Serialize with the original file's indent, trailing newline and line endings (2 spaces, newline, LF for a new file)."""
    indent: str | int = 2
    if original:
        found = re.search(r"^([ \t]+)\S", original, re.MULTILINE)
        if found:
            indent = found.group(1)
    text = json.dumps(doc, indent=indent, ensure_ascii=False)
    if original is None or original.endswith("\n") or not original.strip():
        text += "\n"
    if original and "\r\n" in original:
        text = text.replace("\n", "\r\n")
    return text


def reformat_noise(original: str | None, doc_before: dict) -> bool:
    """True when re-serializing the untouched document would already change the file's text."""
    return bool(original and original.strip()) and dump_settings(doc_before, original) != original


# --- diff and write ------------------------------------------------------------------------------

def unified_diff(old: str | None, new: str, path: Path) -> list[str]:
    return [line.rstrip("\r\n") for line in difflib.unified_diff(
        (old or "").splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path) if old is not None else "/dev/null", tofile=str(path), n=2)]


def diff_rows(lines: list[str]) -> list[dict]:
    """The diff as `diff_line` rows for the renderer (a verbatim schema: the text is shown exactly as it is)."""
    return [{"text": line} for line in lines]


def _backup_names(path: Path):
    """`<name>.bak`, then `<name>.bak.1`, `<name>.bak.2`, ... in the order they are made."""
    yield path.with_name(path.name + ".bak")
    n = 1
    while True:
        yield path.with_name(f"{path.name}.bak.{n}")
        n += 1


def write_with_backup(path: Path, text: str) -> Path | None:
    """Atomically replace `path` with `text` (through a symlink, never replacing it). When the file existed,
    its previous bytes first go to a backup next to it, and a backup is never overwritten: the first is
    `<name>.bak` (so the user's original survives an install followed by an uninstall), later different
    ones are `<name>.bak.1`, `.bak.2`, ... Bytes some backup already holds are not saved twice. Returns the
    backup file that holds the previous bytes, or None when there was nothing to back up."""
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    mode = 0o600
    if path.is_file():
        mode = path.stat().st_mode & 0o777
        previous = path.read_bytes()
        for candidate in _backup_names(path):
            if not candidate.exists():
                _atomic(candidate, previous, mode)
                backup = candidate
                break
            if candidate.is_file() and candidate.read_bytes() == previous:
                backup = candidate
                break
    _atomic(path, text.encode("utf-8"), mode)
    return backup


def _atomic(path: Path, data: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
