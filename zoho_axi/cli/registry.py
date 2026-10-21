"""The subcommand registry: name -> (core command, CLI metadata). Later tickets (T08-T11) add one `Spec`
per command here; the parser, `--help` text and strict flag checking are all derived from it, so a
command needs no argparse code of its own.

A name with a space is a group subcommand (`auth status`). Names are matched word by word. A group may
also have a bare command of its own (`project` next to `project use` / `project list`).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.commands import auth, cache, home, hooks, people, project, session, skill, task, tasks
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
    hook: bool = False  # a Claude Code hook entrypoint: plain output, never an error, always exit 0 (see cli/main.py)


GLOBAL_FLAGS: tuple[Flag, ...] = (
    Flag("--project", "project to use: PROJECT or PORTAL:PROJECT (default: config)", metavar="P"),
    Flag("--fresh", "bypass the cache"),
    Flag("--json", "print JSON instead of TOON"),
    Flag("--full", "show every field and never truncate text"),
    Flag("--debug", "print the Zoho request count to stderr"),
)

REGISTRY: tuple[Spec, ...] = (
    Spec("home", "live overview of the default project; also what bare `zoho-axi` runs", home.home,
         examples=("zoho-axi", "zoho-axi home --json")),
    Spec("auth status", "check config and credentials, and verify the token with Zoho", auth.status,
         flags=(Flag("--offline", "skip the token check (no network)"),),
         examples=("zoho-axi auth status", "zoho-axi auth status --offline")),
    Spec("auth login-help", "how to create the Zoho Self Client and where its values go", auth.login_steps,
         examples=("zoho-axi auth login-help", "zoho-axi auth login-help --json")),
    Spec("project", "overview of the project: status, owner, next milestone, counts", project.overview,
         flags=(Flag("--lists", "also list its tasklists and milestones"),),
         examples=("zoho-axi project", "zoho-axi project --project 'Demo Site' --lists")),
    Spec("project list", "list the projects of the portal (id, name, status)", project.list_projects,
         examples=("zoho-axi project list", "zoho-axi project list --json")),
    Spec("project use", "make a project the default (writes portal_id/project_id to the config file)", project.use,
         args=(Arg("ref", "project name or id, or PORTAL:NAME"),),
         examples=("zoho-axi project use 'Demo Site'", "zoho-axi project use 2200000000123")),
    Spec("people", "list the portal's users and tags (both by default)", people.people,
         flags=(
             Flag("--users", "only users"),
             Flag("--tags", "only tags"),
             Flag("--limit", "rows per list (default 50)", metavar="N"),
         ),
         examples=("zoho-axi people", "zoho-axi people --users --limit 100")),
    Spec("tasks", "list tasks: key, title, status, owner, with totals by status", tasks.tasks,
         flags=(
             Flag("--tasklist", "only this tasklist (id or name)", metavar="ID|NAME"),
             Flag("--milestone", "only this milestone (id or name)", metavar="ID|NAME"),
             Flag("--status", "only this status (a name, or open / closed)", metavar="S"),
             Flag("--owner", "only tasks whose owner name contains NAME", metavar="NAME"),
             Flag("--limit", "rows to show (default 20)", metavar="N"),
             Flag("--page", "Zoho page of 100 tasks (default 1)", metavar="N"),
         ),
         examples=("zoho-axi tasks --status open --owner asha", "zoho-axi tasks --tasklist Auth --limit 50")),
    Spec("task", "one task: status, owner, counts; add slices for comments, history, subtasks, attachments", task.task,
         args=(Arg("ref", "task key (PROJ1-T1000) or numeric id"),),
         flags=(Flag("--comments", "show the last 3 comments (--full: all)"),
                Flag("--history", "show the task's status changes"),
                Flag("--subtasks", "list subtasks (unverified: Zoho cannot filter by parent)"),
                Flag("--attachments", "list attachment names, types and sizes")),
         examples=("zoho-axi task PROJ1-T1000", "zoho-axi task 4000000012345 --comments --full")),
    Spec("cache status", "cache size, entry counts and the oldest entry", cache.status,
         examples=("zoho-axi cache status", "zoho-axi cache status --json")),
    Spec("cache clear", "delete the cache and derived index (needs --yes; never prompts)", cache.clear,
         flags=(Flag("--yes", "confirm the deletion"),),
         examples=("zoho-axi cache clear --yes", "zoho-axi cache clear")),
    Spec("session-start", "Claude Code SessionStart hook: project snapshot from the cache; silent on any failure",
         session.session_start, hook=True,
         examples=("zoho-axi session-start", "zoho-axi hooks install")),
    Spec("hooks install", "add the SessionStart hook to ~/.claude/settings.json (shows the diff; --yes applies)",
         hooks.install, flags=(Flag("--yes", "apply the change (without it nothing is written)"),),
         examples=("zoho-axi hooks install", "zoho-axi hooks install --yes")),
    Spec("hooks uninstall", "remove the SessionStart hook again (shows the diff; --yes applies)",
         hooks.uninstall, flags=(Flag("--yes", "apply the change (without it nothing is written)"),),
         examples=("zoho-axi hooks uninstall", "zoho-axi hooks uninstall --yes")),
    Spec("skill install", "copy the zoho-axi skill to ~/.claude/skills/zoho-axi/ (shows the change; --yes applies)",
         skill.install, flags=(Flag("--yes", "apply the change (without it nothing is written)"),),
         examples=("zoho-axi skill install", "zoho-axi skill install --yes")),
)
