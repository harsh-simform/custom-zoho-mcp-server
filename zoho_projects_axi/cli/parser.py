"""Strict argument parsing on top of stdlib argparse. Nothing here reads stdin or prompts.

The command is resolved from the registry first (words before any command flag); unknown flags are
rejected by our own scan so the error can name the closest valid flag; argparse then only converts
the already-vetted tokens into values. Every failure is a `UsageError` (exit 2), never argparse's own
exit. Global flags are accepted before or after the command.
"""
from __future__ import annotations

import argparse
import difflib
from dataclasses import dataclass
from typing import Any, Sequence

from ..core.errors import UsageError
from .registry import GLOBAL_FLAGS, Flag, Spec

HELP_FLAGS = ("--help", "-h")
HOME = "home"


@dataclass(frozen=True)
class Invocation:
    spec: Spec | None  # None only for top-level help
    help: bool
    project: str | None = None
    fresh: bool = False
    json: bool = False
    full: bool = False
    debug: bool = False
    params: dict[str, Any] | None = None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would print usage and exit(2) itself
        raise UsageError("usage.invalid_arguments", message, hint=f"Run `zoho-projects-axi {self.prog} --help`.")


def _closest(word: str, options: Sequence[str]) -> list[str]:
    return difflib.get_close_matches(word, list(options), n=2, cutoff=0.6)


def _value_flags(flags: Sequence[Flag]) -> set[str]:
    return {f.name for f in flags if f.metavar}


def _command_words(tokens: Sequence[str]) -> list[tuple[int, str]]:
    """(index, word) of each non-flag token before `--`; the value of a global value-flag is not a word."""
    skip_value = _value_flags(GLOBAL_FLAGS)
    words, i = [], 0
    while i < len(tokens) and tokens[i] != "--":
        tok = tokens[i]
        if tok in skip_value:
            i += 1
        elif not tok.startswith("-"):
            words.append((i, tok))
        i += 1
    return words


def resolve(tokens: Sequence[str], registry: Sequence[Spec]) -> tuple[Spec, list[int]]:
    """The spec named by the leading words, and the token indexes those words occupy."""
    words = _command_words(tokens)
    if not words:
        return _by_name(registry, HOME), []
    first_words = list(dict.fromkeys(s.name.split()[0] for s in registry))
    head = words[0][1]
    if head not in first_words:
        for tok in tokens[: words[0][0]]:  # a mistyped global flag before the command is the likelier mistake
            name = tok.split("=", 1)[0]
            if tok.startswith("-") and name not in [f.name for f in GLOBAL_FLAGS] + list(HELP_FLAGS):
                near = _closest(name, [f.name for f in GLOBAL_FLAGS])
                raise UsageError("usage.unknown_flag", f"unknown flag {name}",
                                 hint=(f"Did you mean {near[0]}? " if near else "") + "Run `zoho-projects-axi --help` for the flags.")
        near = _closest(head, first_words)
        hint = (f"Did you mean `zoho-projects-axi {near[0]}`? " if near else "") + "Run `zoho-projects-axi --help` for the commands."
        raise UsageError("usage.unknown_command", f"unknown command {head!r}", hint=hint)
    group = [s for s in registry if s.name.split()[0] == head]
    subs = [s.name.split()[1] for s in group if " " in s.name]
    if any(s.name == head for s in group):
        # A plain command; any further word is its positional argument, unless the command also has
        # subcommands (`project` / `project use X`), in which case a second word naming one selects it.
        if len(words) > 1 and words[1][1] in subs:
            return _by_name(registry, f"{head} {words[1][1]}"), [words[0][0], words[1][0]]
        return _by_name(registry, head), [words[0][0]]
    if len(words) < 2:
        raise UsageError("usage.missing_subcommand", f"{head} needs a subcommand: {', '.join(subs)}",
                         hint=f"Run `zoho-projects-axi {head} {subs[0]}`.")
    sub = words[1][1]
    if sub not in subs:
        near = _closest(sub, subs)
        raise UsageError(
            "usage.unknown_command", f"unknown subcommand {head} {sub!r}",
            hint=(f"Did you mean `zoho-projects-axi {head} {near[0]}`?" if near else f"Subcommands: {', '.join(subs)}."))
    return _by_name(registry, f"{head} {sub}"), [words[0][0], words[1][0]]


def _by_name(registry: Sequence[Spec], name: str) -> Spec:
    return next(s for s in registry if s.name == name)


def _check_flags(tokens: Sequence[str], spec: Spec) -> None:
    known = [*GLOBAL_FLAGS, *spec.flags]
    names = [f.name for f in known] + list(HELP_FLAGS)
    takes_value = _value_flags(known)
    i = 0
    while i < len(tokens) and tokens[i] != "--":
        tok = tokens[i]
        if tok.startswith("-") and len(tok) > 1 and not tok[1:].replace(".", "").isdigit():
            name = tok.split("=", 1)[0]
            if name not in names:
                near = _closest(name, names)
                hint = (f"Did you mean {near[0]}? " if near else "") + f"Run `zoho-projects-axi {spec.name} --help` for the flags."
                raise UsageError("usage.unknown_flag", f"unknown flag {name} for `zoho-projects-axi {spec.name}`", hint=hint)
            if name in takes_value and "=" not in tok:
                i += 1  # its value, even if it starts with '-'
        i += 1


def parse(argv: Sequence[str], registry: Sequence[Spec]) -> Invocation:
    tokens = list(argv)
    spec, consumed = resolve(tokens, registry)
    rest = [t for i, t in enumerate(tokens) if i not in consumed]
    _check_flags(rest, spec)

    before_dd = rest[: rest.index("--")] if "--" in rest else rest
    if any(t in HELP_FLAGS for t in before_dd):
        return Invocation(spec if consumed else None, help=True)  # bare `--help` is the top-level help

    parser = _Parser(prog=spec.name, add_help=False, allow_abbrev=False)
    for flag in (*GLOBAL_FLAGS, *spec.flags):
        if flag.metavar:
            parser.add_argument(flag.name, dest=flag.dest, metavar=flag.metavar)
        else:
            parser.add_argument(flag.name, dest=flag.dest, action="store_true")
    for arg in spec.args:
        parser.add_argument(arg.name, nargs=None if arg.required else "?")
    ns = vars(parser.parse_args(rest))
    own = {f.dest for f in spec.flags} | {a.name for a in spec.args}
    return Invocation(
        spec, help=False,
        project=ns["project"], fresh=ns["fresh"], json=ns["json"], full=ns["full"], debug=ns["debug"],
        params={k: v for k, v in ns.items() if k in own},
    )
