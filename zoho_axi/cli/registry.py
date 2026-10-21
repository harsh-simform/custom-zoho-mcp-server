"""The subcommand registry: name -> (core command, CLI metadata). Later tickets (T08-T11) add one `Spec`
per command here; the parser, `--help` text and strict flag checking are all derived from it, so a
command needs no argparse code of its own.

A name with a space is a group subcommand (`auth status`). Names are matched word by word.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.commands import auth, home
from ..core.commands.base import Command


@dataclass(frozen=True)
class Flag:
    name: str  # "--offline"
    help: str
    metavar: str | None = None  # set when the flag takes a value

    @property
    def dest(self) -> str:
        return self.name[2:].replace("-", "_")


@dataclass(frozen=True)
class Arg:
    name: str  # positional, e.g. "ref"
    help: str
    required: bool = True


@dataclass(frozen=True)
class Spec:
    name: str
    summary: str
    run: Command
    examples: tuple[str, ...]
    args: tuple[Arg, ...] = ()
    flags: tuple[Flag, ...] = ()


GLOBAL_FLAGS: tuple[Flag, ...] = (
    Flag("--project", "project to use: PROJECT or PORTAL:PROJECT (default: config)", metavar="P"),
    Flag("--fresh", "bypass the cache"),
    Flag("--json", "print JSON instead of TOON"),
    Flag("--full", "show every field and never truncate text"),
    Flag("--debug", "print the Zoho request count to stderr"),
)

REGISTRY: tuple[Spec, ...] = (
    Spec("home", "live overview (placeholder until T10); also what bare `zoho-axi` runs", home.home,
         examples=("zoho-axi", "zoho-axi home --json")),
    Spec("auth status", "check config and credentials, and verify the token with Zoho", auth.status,
         flags=(Flag("--offline", "skip the token check (no network)"),),
         examples=("zoho-axi auth status", "zoho-axi auth status --offline")),
    Spec("auth login-help", "how to create the Zoho Self Client and where its values go", auth.login_steps,
         examples=("zoho-axi auth login-help", "zoho-axi auth login-help --json")),
)
