"""`zoho-projects-axi cache status` and `cache clear [--yes]`: T07's functions, shaped into commands. They never
touch Zoho (so no credentials are needed) and `clear` never prompts: without `--yes` it is a usage error."""
from __future__ import annotations

import os

from ..cache import cache_clear, cache_status, default_cache_path
from ..result import Result
from .base import Context


def _path(ctx: Context):
    return default_cache_path(os.environ if ctx.env is None else ctx.env)


def status(ctx: Context) -> Result:
    return cache_status(_path(ctx))


def clear(ctx: Context) -> Result:
    return cache_clear(bool(ctx.params.get("yes")), _path(ctx))
