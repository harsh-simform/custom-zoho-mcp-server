"""`zoho-axi skill install [--yes]` (T14): copy the packaged skill to `~/.claude/skills/zoho-axi/SKILL.md`.

The skill ships as package data (`zoho_axi/skills/zoho-axi/SKILL.md`) and is read through
`importlib.resources`, so it works from a wheel as well as from a source checkout. Like `hooks install`
it shows what it would do and writes only with `--yes`; an identical copy is left untouched, and a
different copy is replaced after being saved next to it as `SKILL.md.bak`. It never prompts.
"""
from __future__ import annotations

import os
from importlib import resources

from .. import claude_settings as cs
from ..result import Hint, Result
from .base import Context


def packaged_skill() -> str:
    return (resources.files("zoho_axi") / "skills" / "zoho-axi" / "SKILL.md").read_text(encoding="utf-8")


def install(ctx: Context) -> Result:
    env = os.environ if ctx.env is None else ctx.env
    path = cs.skill_file(env)
    wanted = packaged_skill()
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    payload: dict = {"file": str(path)}
    if current == wanted:
        payload["status"] = "up to date"
        return Result("skill", payload)

    payload["change"] = "create" if current is None else "update"
    if current is not None:
        payload["diff"] = cs.diff_rows(cs.unified_diff(current, wanted, path))
    if not ctx.params.get("yes"):
        payload["status"] = "dry run"
        return Result("skill", payload, help=[Hint("skill install", {"yes": True})])

    if cs.write_with_backup(path, wanted):
        payload["backup"] = f"{path}.bak"
    payload["status"] = "installed"
    return Result("skill", payload)
