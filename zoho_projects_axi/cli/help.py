"""`--help` text, generated from the registry so it cannot drift from what the parser accepts.
Per-command help is short by design (usage, flags, examples); top-level help fits one screen."""
from __future__ import annotations

from typing import Sequence

from .registry import GLOBAL_FLAGS, Spec


def command_help(spec: Spec) -> str:
    usage = ["zoho-projects-axi", spec.name]
    usage += [f"<{a.name}>" if a.required else f"[{a.name}]" for a in spec.args]
    usage += [f"[{f.name}{' ' + f.metavar if f.metavar else ''}]" for f in spec.flags]
    lines = [f"usage: {' '.join(usage)}", spec.summary]
    if spec.args or spec.flags:
        lines.append("options:")
        lines += [f"  {a.name}  {a.help}" for a in spec.args]
        lines += [f"  {f.name}{' ' + f.metavar if f.metavar else ''}  {f.help}" for f in spec.flags]
    lines.append("global flags: " + " ".join(f.name for f in GLOBAL_FLAGS) + " --help (see `zoho-projects-axi --help`)")
    lines.append("examples:")
    lines += [f"  {e}" for e in spec.examples]
    return "\n".join(lines)


def top_help(registry: Sequence[Spec]) -> str:
    width = max(len(s.name) for s in registry)
    lines = [
        "zoho-projects-axi: read-only Zoho Projects for agents. Output is TOON; --json gives JSON.",
        "usage: zoho-projects-axi [COMMAND] [flags]   (no command = home)",
        "commands:",
        *[f"  {s.name.ljust(width)}  {s.summary}" for s in registry],
        "global flags:",
        *[f"  {(f.name + (' ' + f.metavar if f.metavar else '')).ljust(11)}  {f.help}" for f in GLOBAL_FLAGS],
        f"  {'--help'.ljust(11)}  show help",
        "Run `zoho-projects-axi COMMAND --help` for a command's flags and examples.",
    ]
    return "\n".join(lines)
