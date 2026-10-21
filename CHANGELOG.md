# Changelog

## [Unreleased]

### Added

- **`zoho-axi auth login`: browser sign-in.** Runs the OAuth authorization-code flow with PKCE and a loopback
  redirect (`http://localhost:8765/callback`, `--port N` to change, `--no-browser` to only print the URL), checks
  `state`, and saves the `refresh_token` into the config file without disturbing its comments or mode. Requests
  read scopes only. Replaces the manual Self Client code exchange; a one-time client registration (id and
  secret) in the Zoho API console is still needed because Zoho has no dynamic client registration.

## [0.2.0] - 2026-10-21

0.2.0 is a rewrite around a shared command layer. The new `zoho-axi` CLI and the MCP server run the same
commands and return the same compact text. It is a breaking release for anything that calls the MCP server.

### Breaking: the MCP tool surface

- **15 tools became 6.** The server now exposes `project`, `tasks`, `task`, `bug`, `people` and `resolve`. Each
  tool is derived from the CLI command of the same name, so its arguments are that command's positional
  arguments and flags plus `project`, `fresh`, `full` and `format` (`toon` or `json`).
- **Removed tools:** `configure_oauth`, `list_portals`, `list_projects`, `select_portal_and_project`,
  `switch_portal`, `list_selected_portals`, `remove_portal_selection`, `reset_selection`, `get_project_context`,
  `get_tasklist_context`, `get_milestone_context`, `get_task_context`, `get_bug_context`, `list_users` and
  `list_tags`. Use `task` and `bug` (with `comments`, `history`, `subtasks`, `attachments`, `images` and `task`
  slices) instead of the `get_*_context` tools, `project` with `lists=true` for tasklists and milestones, and
  `people` for users and tags.
- **No configure or select tools, and no session state.** The `NOT_CONFIGURED -> OAUTH_READY -> CONTEXT_SELECTED`
  stage machine is gone. The server starts from the config file (or environment) alone; a call that needs a
  project takes `project=` or uses the configured default. There is no project list or project switch over MCP:
  use the CLI (`zoho-axi project list`, `zoho-axi project use <name>`), which writes the default to the config file.
- **Output is TOON text, not JSON dicts**, with a short `help[]` block of next calls. Pass `format="json"` for JSON.
  Failures are tool errors carrying a structured `error / message / hint` block.
- **Images are opt-in.** `images=true` on `task` or `bug` returns the images inline; nothing is returned or
  written by default (0.1.x always downloaded up to five).
- Measured effect on the always-loaded tool schemas: 651 tokens for the six tools against 1,421 for the fifteen
  (estimated as characters / 4; see the README).

### Added

- **`zoho-axi` CLI** (also installed as `zoho`): `home`, `project`, `project list`, `project use`, `people`,
  `tasks`, `task`, `bug`, `resolve`, `auth status`, `auth login-help`, `cache status`, `cache clear`, `hooks
  install`, `hooks uninstall`, `skill install`, `session-start` and `mcp`. Per-command token budgets are enforced
  by the test suite. Exit codes: 2 usage, 3 auth or config, 4 not found, 5 upstream, 6 rate limited.
- **`zoho-axi mcp`**, the canonical way to start the MCP server over stdio.
- **Config file** `~/.config/zoho-axi/config.toml` (mode 600; a looser mode produces a warning) with
  `ZOHO_AXI_<KEY>` environment overrides and a data-center allowlist (`com`, `eu`, `in`, `com.au`, `jp`). See
  `config.example.toml`. It replaces `.env.example`.
- **Response cache** (SQLite, `~/.cache/zoho-axi/cache.sqlite`) with per-kind lifetimes, `--fresh` to bypass it
  and `zoho-axi cache clear --yes` to delete it. 0.1.x persisted nothing.
- **Client-side rate limiter** (100 requests per 2 minutes) and bounded 429 retry, so a runaway agent fails fast
  instead of getting the API locked for 30 minutes.
- **Claude Code integration:** a SessionStart hook (`hooks install`) and a bundled skill (`skill install`). Both
  show a diff first and only write with `--yes`.
- **Test harness:** synthetic recorded-style fixtures, goldens, token-budget helpers, an offline guard, a
  read-only fixture recorder (`scripts/record_fixture.py`) and an opt-in live smoke (`ZOHO_AXI_LIVE=1`).

### Changed

- The distribution is renamed from `zoho-projects-mcp` to `zoho-axi`, and the package is now `zoho_axi`.
- The read-only guarantee is enforced at the transport: only GET requests (plus the OAuth token refresh) can be sent.
- Python 3.10 is supported through the `tomli` fallback; 3.11 and later use `tomllib`.

### Removed

- `python -m zoho_mcp` and the `zoho-projects-mcp` script, along with the `zoho_mcp` package. Register the server
  with `zoho-axi mcp` instead.

### Deprecated

- The `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`, `ZOHO_PORTAL_ID`, `ZOHO_PROJECT_ID`, `ZOHO_DC`
  and `ZOHO_MCP_*` environment variables and the repo-root `.env` file are read only as a fallback, with a warning.
  Move the values into the config file.

### Known limitations

All test fixtures are synthetic and no live Zoho account was used during development. See "Known limitations"
in the README for the Zoho behaviours that remain unverified.

## [0.1.0]

First version: a read-only MCP server with 15 tools, credentials from `.env` and in-memory session state.
