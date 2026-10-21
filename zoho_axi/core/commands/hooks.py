"""`zoho-axi hooks install [--yes]` and `hooks uninstall [--yes]` (T14, decision D13).

Both compute the edit to `~/.claude/settings.json` first and print it as a unified diff. Without `--yes`
that is all they do: nothing is written, not even a backup or the `.claude` directory. With `--yes` the
file is replaced atomically after its previous bytes were copied to `settings.json.bak`; when the entry
is already there (install) or not there (uninstall) nothing is written at all, so re-running is a
no-op. They never prompt: there is no stdin in the CLI.

The home directory is the `HOME` of the environment the command was started with (see
core/claude_settings.py, which also refuses any target that resolves outside it).
"""
from __future__ import annotations

import os

from .. import claude_settings as cs
from ..result import Hint, Result
from .base import Context


def _run(ctx: Context, *, install: bool) -> Result:
    env = os.environ if ctx.env is None else ctx.env
    path = cs.settings_file(env)
    original = path.read_text(encoding="utf-8") if path.is_file() else None
    doc = cs.parse_settings(original, path)
    noisy = cs.reformat_noise(original, doc)
    changed = cs.add_hook(doc, path) if install else cs.remove_hook(doc, path)

    warnings = []
    if changed and noisy:
        warnings.append(f"{path.name} uses a layout JSON re-serialization does not reproduce, so the diff also shows "
                        "re-indented lines; values are unchanged")
    verb = "install" if install else "uninstall"
    payload: dict = {"file": str(path)}
    if not changed:
        payload["status"] = "already installed" if install else "not installed"
        return Result("hooks", payload, warnings=warnings)

    updated = cs.dump_settings(doc, original)
    payload["diff"] = cs.diff_rows(cs.unified_diff(original, updated, path))
    if not ctx.params.get("yes"):
        payload["status"] = "dry run"
        return Result("hooks", payload, warnings=warnings, help=[Hint(f"hooks {verb}", {"yes": True})])

    backed_up = cs.write_with_backup(path, updated)
    payload["status"] = "installed" if install else "removed"
    if backed_up:
        payload["backup"] = f"{path}.bak"
    return Result("hooks", payload, warnings=warnings, help=[Hint("session-start")] if install else [])


def install(ctx: Context) -> Result:
    return _run(ctx, install=True)


def uninstall(ctx: Context) -> Result:
    return _run(ctx, install=False)
