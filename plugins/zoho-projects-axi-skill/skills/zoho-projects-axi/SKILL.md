---
name: zoho-projects-axi
description: Read Zoho Projects (tasks, bugs, projects, people) with the read-only `zoho-projects-axi` CLI. Use when a prompt names a Zoho task or bug key such as PROJ1-T1000, mentions a Zoho project, or asks about the status, owner, due date, comments or history of Zoho work items.
---

# zoho-projects-axi: Zoho Projects for agents

`zoho-projects-axi` is a read-only CLI (it never creates or changes anything in Zoho). Its output is TOON, a compact
table-like text; add `--json` only if you need to parse it. Every answer ends with `help[]`: the next
commands worth running. Prefer those over guessing.

## When to use it

- The user mentions a task or bug key (like `PROJ1-T1000`), a Zoho project, a milestone, a tasklist.
- The user asks what is open, overdue, due soon, or who owns something in Zoho.
- You need the description, comments or history of a ticket before working on it.

Do not use it to change tickets (it cannot) or for non-Zoho trackers.

## Start with `resolve`

Pass the user's raw text to `zoho-projects-axi resolve "<their prompt>"`. It extracts task keys, bug keys and project
names with plain regexes (no guessing) and prints the exact commands to run next. Run those. If it finds
nothing, use `zoho-projects-axi` (the live home view) or `zoho-projects-axi tasks`.

## Commands

| Command | What it gives you |
|---|---|
| `zoho-projects-axi` | live home: default project, open/overdue counts, what is due soon |
| `zoho-projects-axi resolve "<text>"` | keys and projects found in free text, with suggested commands |
| `zoho-projects-axi task <KEY>` | one task: title, status, owner, priority, due, tasklist, description |
| `zoho-projects-axi bug <KEY>` | one bug: title, status, severity, owner, description |
| `zoho-projects-axi tasks` | task list (key, title, status, owner) with counts by status |
| `zoho-projects-axi project` | project overview: status, owner, next milestone, counts |
| `zoho-projects-axi people` | portal users and tags |
| `zoho-projects-axi auth status` | is the config complete and the token valid |

Flags and examples live in `zoho-projects-axi COMMAND --help`; read that instead of guessing a flag. Unknown flags
fail with exit code 2 and a "did you mean" hint, and nothing ever prompts.

## Ask for slices, not everything

The default output is lean on purpose (3-4 fields per row, long text cut with a size hint). Widen only
what the question needs:

- `zoho-projects-axi task <KEY> --comments` for discussion, `--history` for status changes, `--subtasks` for
  children, `--attachments` for file names. Combine them only when you need more than one.
- `--full` shows every field and never truncates text. Use it last, on one item, not on a list.
- `zoho-projects-axi tasks --status open --owner <name> --limit 10` narrows a list before you read it; the totals
  line already counts everything, so you rarely need more rows.
- Images are not fetched unless you ask for them.

## Saving tokens

- Do not run `zoho-projects-axi tasks` just to look up one task; use its key with `task`.
- Read the counts line before the rows: it often answers "how many".
- Results come from a local cache, so repeating a command is cheap. Add `--fresh` only when you suspect the
  data changed in the last few minutes.
- A line like `description: "... (+1.8k chars, --full)"` means text was cut; fetch the rest only if it matters.
- Errors are short: `error`, `message`, `hint`. Do what the hint says (often `zoho-projects-axi auth login-help`).

## Project context

The default project comes from the user's config file (`zoho-projects-axi project use <name>` sets it). Pass
`--project NAME` or `--project PORTAL:ID` to look at another project for one call. If a session-start
hook is installed, the first lines of the conversation already show the active project and its status
tally; that is a cached snapshot, so run `zoho-projects-axi` for live numbers.

## Not configured?

If a command says credentials are missing, run `zoho-projects-axi auth login-help` and relay the steps to the user. If only the refresh token is missing or invalid, tell the user to run `zoho-projects-axi auth login` (it opens their browser; do not run it for them unless asked).
Never ask the user to paste secrets into the chat.
