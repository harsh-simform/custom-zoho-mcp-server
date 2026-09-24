# Zoho Projects MCP Server

A read-only [MCP](https://modelcontextprotocol.io) server that exposes Zoho Projects
data (portals, projects, tasklists, milestones, tasks, bugs, users, tags) to Claude —
so it can look up task/bug context directly instead of you copy-pasting it in.

No write operations are performed against Zoho — this server only reads.

Full technical spec: [docs/zoho-projects-mcp-plan.md](docs/zoho-projects-mcp-plan.md).

## Features

- **One-call context fetch** — `get_task_context` / `get_bug_context` return details,
  comments, subtasks/linked task, status history, and attachments in a single call,
  instead of chaining several lookups yourself.
- **Inline screenshot viewing** — image attachments on a task/bug (screenshots, etc.)
  are downloaded and returned as actual images in the same response, so Claude can
  visually inspect them without a follow-up call. Capped by
  `ZOHO_MCP_MAX_CONTEXT_IMAGES` (default 5) and `ZOHO_MCP_MAX_ATTACHMENT_BYTES`
  (default 8MB/image); anything skipped or oversized is noted in the response's
  `errors` list instead of failing the whole call.
- **Multi-portal / multi-project selection** — select several portal/project pairs in
  one running instance and switch the active one with `switch_portal`, without
  re-entering OAuth or re-validating each time. (In-memory for that session only —
  see Features note on state below.)
- **Human-readable key resolution** — task/bug keys like `AD1-T1153` are resolved to
  Zoho's internal IDs automatically (needs the `ZohoProjects.search.READ` scope).
- **Guarded state machine** — tools are gated behind
  `NOT_CONFIGURED → OAUTH_READY → CONTEXT_SELECTED`. Calling a tool before its
  required stage returns a plain-language error telling you which step to do first,
  instead of a raw API failure.
- **Legacy endpoint fallback** — `client.py` tries the current v3 API first and falls
  back to the legacy `/restapi` path on 404, since Zoho's public docs still only
  document the legacy path for some endpoints (milestones, comments, attachments).
- **Automatic retry** — transient HTTP failures are retried (`MAX_RETRIES`, default 3)
  before surfacing an error.
- **Secure logging** — every tool call, OAuth refresh, HTTP request/retry, and stage
  transition is logged to **stderr only** (stdout is reserved for the MCP JSON-RPC
  wire protocol). Secrets (`client_secret`, `refresh_token`, `access_token`) are
  redacted by parameter name before logging, so they never end up in logs.
- **Everything from `.env`, nothing persisted to disk** — OAuth credentials and the
  default portal/project load from `.env` (see `.env.example`) at startup, so no
  manual `configure_oauth` / `select_portal_and_project` call is needed for the
  common case. There is no state file at all — the server keeps its session state
  (auth, active portal/project) in memory only, for that process's lifetime.
  `configure_oauth` and `select_portal_and_project`/`switch_portal` still exist as
  manual, in-memory-only overrides for a single running session (e.g. testing a
  different token, or picking a project you didn't set as the `.env` default), and
  simply reset to the `.env` defaults on restart.

## ⚠️ Before first run

Verify Zoho's current base URL / auth header and whether the legacy `/restapi` paths
(comments, milestones, attachments) still work — see plan §0. Adjust
`zoho_mcp/config.py` / `zoho_mcp/client.py` if they've changed.

## Prerequisites

- Python ≥ 3.10
- A Zoho account with access to the Projects portal(s) you want to query
- A registered Zoho Self Client (see Setup below)

## Setup

1. **Register a Self Client** at the [Zoho API Console](https://api-console.zoho.com),
   request the scopes listed in plan §1 — including `ZohoProjects.search.READ`, needed
   to resolve a task/bug's human-readable key (e.g. `AD1-T1153`) to its internal ID —
   and generate a refresh token. Note down the **client ID**, **client secret**, and
   **refresh token**.

2. **Clone and install** into a virtual environment:

   ```bash
   git clone <this-repo-url> zoho-mcp
   cd zoho-mcp
   python -m venv venv && source venv/bin/activate
   pip install -e .
   ```

3. **Create `.env`** from the template and fill in the values from step 1:

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

   Then edit `.env`:

   ```
   ZOHO_CLIENT_ID=...
   ZOHO_CLIENT_SECRET=...
   ZOHO_REFRESH_TOKEN=...

   # optional — skip select_portal_and_project by setting a default here
   ZOHO_PORTAL_ID=...
   ZOHO_PROJECT_ID=...
   ```

   `.env` is gitignored — never commit it. If you'd rather not use a default
   portal/project, leave those two blank and pick interactively at runtime instead
   (see First-time usage flow below).

4. **Run it standalone** (optional, to sanity-check it starts):

   ```bash
   python -m zoho_mcp
   ```

   It should start and wait on stdin/stdout for MCP messages, with startup logs on
   stderr. Ctrl-C to stop.

## Register with Claude Code — globally (recommended)

This server is meant to be usable from **any project, any directory** — not just when
Claude happens to be started from inside this repo. Register it once, at **user
scope**, and it's available everywhere:

```bash
claude mcp add --scope user custom-zoho-project-mcp-server \
  -- /absolute/path/to/zoho-mcp/venv/bin/python -m zoho_mcp
```

That's the whole global setup — no extra env flags needed on the `claude mcp add`
command itself. Two things make this work reliably from any cwd:

- **Use the absolute path** to the venv's Python (`/absolute/path/to/zoho-mcp/venv/bin/python`),
  not a relative one — the command needs to resolve the same way regardless of which
  directory Claude was started from.
- **`.env` is anchored to the repo root**, not to the process's current working
  directory (see `config.py`: `Path(__file__).resolve().parent.parent / ".env"`). So
  once `.env` is filled in (Setup, step 3), OAuth and the default project load the
  same way whether Claude was launched from this repo, some other project's directory,
  or anywhere else. Nothing to configure per-directory.

Verify it's visible globally:

```bash
claude mcp list
```

`custom-zoho-project-mcp-server` should show up regardless of which directory you run
that from. If a session was already open in another directory before you registered
it, restart that session to pick up the new server.

### Claude Code — project-local (alternative)

If you'd rather this only be available inside this one repo, run the same command
without `--scope user` (or with `--scope project`/`--scope local`) from inside this
directory.

### Claude Desktop config

Add to Claude Desktop's MCP config file:

```json
{
  "mcpServers": {
    "custom-zoho-project-mcp-server": {
      "command": "/absolute/path/to/zoho-mcp/venv/bin/python",
      "args": ["-m", "zoho_mcp"]
    }
  }
}
```

Restart Claude Desktop (or start a new Claude Code session) after adding or changing
the config for it to take effect.

## First-time usage flow

**If `.env` has all five values filled in** (client ID/secret, refresh token, portal
ID, project ID): nothing to do — OAuth and the active project are both loaded
automatically at startup. Go straight to calling `get_project_context`,
`get_tasklist_context`, `get_milestone_context`, `get_task_context`, `get_bug_context`,
`list_users`, or `list_tags`.

**If you left `ZOHO_PORTAL_ID` / `ZOHO_PROJECT_ID` blank** (OAuth still auto-loads from
`.env`), pick a project interactively once per session:

1. `list_portals()` → pick a portal, then `list_projects(portal_id)` → pick a project.
2. `select_portal_and_project(portal_id, project_id)` — validates and makes it active.
3. Now call any of the context tools listed above.

**If you're not using `.env` at all**, configure OAuth manually first (in-memory only,
for that session):

1. `configure_oauth(client_id, client_secret, refresh_token)`.
2. `list_portals()` → `list_projects(portal_id)` → `select_portal_and_project(portal_id,
   project_id)`.
3. Now call any of the context tools listed above.

Optional, once more than one portal/project has been selected (in-memory for the
current session only — resets to the `.env` default, if any, on restart):
- `list_selected_portals()` — see everything selected and which is active.
- `switch_portal(portal_id, project_id)` — move the active pair between ones already
  selected, without re-validating.
- `remove_portal_selection(portal_id, project_id)` — drop one pair.
- `reset_selection()` — clear all selections without losing OAuth config.

## Tool reference

| Tool | Requires | Purpose |
|---|---|---|
| `configure_oauth` | — | Validate and store OAuth credentials. Must succeed first. |
| `list_portals` | OAuth | List portals available to this account. |
| `list_projects` | OAuth | List projects in a portal. |
| `select_portal_and_project` | OAuth | Validate and activate a portal/project. |
| `switch_portal` | — | Switch active pair among already-selected ones. |
| `list_selected_portals` | — | Show all selected pairs and which is active. |
| `remove_portal_selection` | — | Drop one selected pair. |
| `reset_selection` | — | Clear all selections, keep OAuth config. |
| `get_project_context` | Context selected | Project details + tasklists + milestones + users + tags. |
| `get_tasklist_context` | Context selected | Tasklist metadata + every task in it. |
| `get_milestone_context` | Context selected | Milestone metadata + every task under it. |
| `get_task_context` | Context selected | Full task context (details, comments, subtasks, status history, attachments incl. inline images). |
| `get_bug_context` | Context selected | Full bug context (details, comments, attachments incl. inline images, linked task). |
| `list_users` | OAuth | List users in the current portal. |
| `list_tags` | OAuth | List tags in the current portal. |

## Logging

Env vars:
- `ZOHO_MCP_LOG_LEVEL` (default `INFO`; set `DEBUG` for per-request logs)
- `ZOHO_MCP_LOG_FILE` (optional; also writes logs to this file)

```bash
ZOHO_MCP_LOG_LEVEL=DEBUG python -m zoho_mcp 2>server.log
```

## Other environment variables

- `ZOHO_DC` (default `com`) — Zoho data center region: `com`, `eu`, `in`, `com.au`, `jp`.
- `ZOHO_MCP_ENV_FILE` (default: `.env` at the repo root) — override where `.env` is
  read from, if you want it somewhere else.
- `ZOHO_MCP_MAX_CONTEXT_IMAGES` (default `5`) — max inline images returned per
  task/bug context call.
- `ZOHO_MCP_MAX_ATTACHMENT_BYTES` (default `8388608`, i.e. 8MB) — max size per inline
  image.

## Project layout

```
zoho_mcp/
├── __init__.py
├── __main__.py          # python -m zoho_mcp
├── server.py            # tool registration + gating + call logging
├── auth.py              # OAuth token exchange/refresh
├── client.py            # Zoho Projects REST wrapper
├── state.py             # in-memory session state (env-driven, nothing persisted)
├── config.py            # base URLs, data center, paths, log config
├── logging_config.py    # stderr/file logging setup
└── context_builders.py  # merges multi-endpoint entity context
```

## Out of scope (v1)

Write operations, time logs, forums/documents/events. See plan §11.
