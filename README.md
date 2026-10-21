# zoho-projects-axi

Read-only [Zoho Projects](https://www.zoho.com/projects/) for AI agents. Two front-ends share one command layer:

- **`zoho-projects-axi`**, a command-line tool whose output is compact [TOON](https://github.com/toon-format/toon) text
  (`--json` gives JSON), built to cost an agent few tokens.
- **A six-tool MCP server** (`zoho-projects-axi mcp`) that returns the same text, for MCP clients such as Claude Code and
  Claude Desktop.

It never creates or changes anything in Zoho: only GET requests (plus the OAuth token refresh) can leave the process.

Install it as a Claude Code plugin in two commands and sign in with your browser: see [Install](#install). Pick the
skill + CLI plugin (cheapest on tokens) or the MCP plugin (native tools).

Version 0.2.0 is a breaking rewrite of the 0.1.x MCP server. See [CHANGELOG.md](CHANGELOG.md).

## Why

An agent that reads tickets pays for every character of tool output and for every tool schema loaded into every
session. This project keeps both small and checks that in the test suite.

Numbers below are **measured by this repository's own tests**, on **synthetic fixtures** (see
[Known limitations](#known-limitations-and-what-is-unverified)). The estimator everywhere is a deterministic proxy,
`len(text) / 4` rounded up. It is not a tokenizer: it under-counts text dense in ids, digits and punctuation, so
read the numbers as regression guards and relative sizes, not billing figures.

| What | Measured (tokens) | Budget enforced | Source |
|---|---|---|---|
| Tool schemas, the 6 MCP tools together | 651 (2,601 chars) | 800 | `tests/test_mcp_server.py` |
| Tool schemas, the 15 tools of 0.1.x (for comparison) | 1,421 (5,686 chars) | none | recorded in `tests/test_mcp_server.py` |
| `task` default view (largest synthetic task: 6k-char description, 30 comments) | 439 | 600 | `tests/test_cmd_task.py` |
| `task --comments` (30 comments) | 721 | 900 | `tests/test_cmd_task.py` |
| `bug` default view (same shape) | 334 | 500 | `tests/test_cmd_bug.py` |
| `bug --comments` (30 comments) | 617 | 900 | `tests/test_cmd_bug.py` |
| `tasks` list, 20 rows | 314 | 500 | `tests/test_cmd_fixtures.py` |
| `home` / `project` overview | 75 / 74 | 300 / 400 | `tests/test_cmd_fixtures.py` |
| SessionStart hook output | 83 | 150 | `tests/test_session_start.py` |
| `--help` of one command (recorded for `task`: 180, `bug`: 166) | 166 to 180 | 250 per command | `tests/test_cmd_task.py`, `tests/test_cmd_bug.py` |

Run the suite to see every measured/allowed pair (`venv/bin/python -m pytest tests -q` prints a "token budgets"
table). The estimator and its weakness are described in [tests/README.md](tests/README.md). The README does not
claim anything about the size of 0.1.x tool *output*: only its tool schemas were measured.

How the savings are made: lean default views with explicit slices (`--comments`, `--history`, ...), long text cut
with a size hint, a local response cache, `resolve` to turn free text into exact commands without a network call,
and `help[]` lines that tell the agent the next command instead of making it guess.

## Install

Pick one. Both need [uv](https://docs.astral.sh/uv/) (`brew install uv`) and run zoho-projects-axi through it, so there
is nothing else to install. Once you are signed in (below), Zoho opens in your browser once and you are done.

### Option 1: skill + CLI (lightest on tokens)

```text
/plugin marketplace add harsh-simform/zoho-projects-axi
/plugin install zoho-projects-axi-skill@zoho-projects-axi
```

Claude gets the `zoho-projects-axi` command on its Bash PATH, a skill that teaches it `resolve` and slices, and a
SessionStart hook with a cache-only project snapshot (about 83 tokens). Nothing is loaded into the session until a
Zoho question comes up. Tip: allow `Bash(zoho-projects-axi:*)` in your permissions to avoid prompts.

### Option 2: MCP server (native tools, no Bash needed)

```text
/plugin marketplace add harsh-simform/zoho-projects-axi
/plugin install zoho-projects-axi-mcp@zoho-projects-axi
```

Claude gets six tools (`project`, `tasks`, `task`, `bug`, `people`, `resolve`). Their schemas cost about 650 tokens
in every session, but the tools show up natively, return images inline and work where there is no shell. Use this
option for Claude Desktop-style clients; install only one of the two plugins.

### Sign in

In either option run `/zoho-projects-axi-skill:login` (or `/zoho-projects-axi-mcp:login`), or in a terminal:

```bash
uvx --from git+https://github.com/harsh-simform/zoho-projects-axi zoho-projects-axi auth login
```

Zoho's consent page opens in your browser and the refresh token is saved for you. Read-only scopes only. If a build
ships no Zoho client, `zoho-projects-axi auth login-help` shows the one-time registration (see [Configure](#configure)).

### Without the plugins

Requires Python 3.10 or newer. Install from git as an isolated tool, which puts `zoho-projects-axi` (and the short
alias `zoho`) on your `PATH`:

```bash
uv tool install git+https://github.com/harsh-simform/zoho-projects-axi
```

or

```bash
pipx install git+https://github.com/harsh-simform/zoho-projects-axi
```

Upgrade with `uv tool upgrade zoho-projects-axi` or `pipx upgrade zoho-projects-axi`. Check the install:

```bash
zoho-projects-axi --help
zoho-projects-axi auth status --offline
```

### Renamed from zoho-axi

The project, command, package and plugins were all `zoho-axi` before. Still working, with a deprecation warning:
the `zoho-axi` command, `ZOHO_AXI_*` environment variables and a config at `~/.config/zoho-axi/config.toml`. Move the
config to `~/.config/zoho-projects-axi/config.toml` and rename the variables when convenient. An old SessionStart
hook (`zoho-axi session-start`) is recognized by `hooks uninstall`; `hooks install` then replaces it.

## Configure

All settings live in one TOML file, `~/.config/zoho-projects-axi/config.toml` (or `$XDG_CONFIG_HOME/zoho-projects-axi/config.toml`).
[`config.example.toml`](config.example.toml) lists every setting, commented, with its default.

If your build ships a Zoho client (`zoho-projects-axi auth login-help` says so), skip to step 3: `auth login` needs no
setup. Otherwise, or to use your own client:

1. Register a client once at <https://api-console.zoho.com> (use `.eu`, `.in`, `.com.au` or `.jp` for your data
   center): add a **Server-based Applications** client with the Authorized Redirect URI
   `http://localhost:8765/callback`. `zoho-projects-axi auth login-help` prints these steps with your data center filled in.
2. Put the client's id and secret in the config file and lock it down (the refresh token is added for you in step 3):

   ```bash
   mkdir -p ~/.config/zoho-projects-axi
   curl -sO https://raw.githubusercontent.com/harsh-simform/zoho-projects-axi/main/config.example.toml
   cp config.example.toml ~/.config/zoho-projects-axi/config.toml
   chmod 600 ~/.config/zoho-projects-axi/config.toml
   ```

   Edit `client_id`, `client_secret`, and `dc` if your account is not on `com`. Or skip the file and set
   `ZOHO_PROJECTS_AXI_CLIENT_ID` / `ZOHO_PROJECTS_AXI_CLIENT_SECRET`.
3. Sign in:

   ```bash
   zoho-projects-axi auth login
   ```

   It opens Zoho's consent page in your browser (the URL is also printed, for `--no-browser` or a remote shell),
   catches the redirect on a one-shot listener bound to `127.0.0.1` (PKCE and a `state` check protect the code), and
   saves the `refresh_token` into the config file, keeping its comments and mode 600. Only read scopes are requested:
   `ZohoProjects.{portals,projects,tasks,tasklists,milestones,bugs,users,tags,search}.READ`. Use `--port N` if 8765
   is taken (register `http://localhost:N/callback` too). If your account is in another data center, the sign-in
   finds it and saves `dc` for you.

   No browser (ssh, CI)? Create a **Self Client** in the same console instead, exchange its code once at
   `https://accounts.zoho.<dc>/oauth/v2/token` (`grant_type=authorization_code`, `client_id`, `client_secret`,
   `code`) and put the `refresh_token` in the config yourself. Treat it like a password.
4. Check it, pick a default project and look around:

   ```bash
   zoho-projects-axi auth status
   zoho-projects-axi project list
   zoho-projects-axi project use "Demo Site"
   zoho-projects-axi
   zoho-projects-axi task PROJ1-T1000
   ```

   `project use` writes `portal_id` and `project_id` into the config file (the only thing the CLI ever writes
   there, preserving comments). You can also pass `--project NAME` or `--project PORTAL:ID` on any call.

### Settings

| Key | Default | Meaning |
|---|---|---|
| `client_id`, `client_secret` | none (required) | the client you registered in the Zoho API console |
| `refresh_token` | none (required) | written by `zoho-projects-axi auth login` (or a Self Client's token, by hand) |
| `dc` | `com` | Zoho data center: `com`, `eu`, `in`, `com.au` or `jp`. An allowlist, because it is placed in the hostnames that receive your credentials |
| `portal_id`, `project_id` | none | default project (set by `project use`) |
| `max_images` | 5 | images per `--images` call; 0 disables |
| `max_attachment_bytes` | 8388608 | skip any single image bigger than this |
| `ttl_long_seconds` | 86400 | cache lifetime of portals, projects, users, tags, tasklists, milestones |
| `ttl_task_seconds` | 300 | cache lifetime of tasks, bugs, search |
| `ttl_comment_seconds` | 120 | cache lifetime of comments, attachments, status history |
| `log_level`, `log_file` | `INFO`, none | MCP server logging (stderr, secrets redacted, optional file) |

Precedence, highest first: command-line flags, `ZOHO_PROJECTS_AXI_<KEY>` environment variables (for example
`ZOHO_PROJECTS_AXI_DC`), the config file, then the deprecated legacy sources and defaults. `zoho-projects-axi auth status --offline`
shows where each setting came from without printing secrets.

**Deprecated legacy sources.** The 0.1.x environment variables (`ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`,
`ZOHO_REFRESH_TOKEN`, `ZOHO_PORTAL_ID`, `ZOHO_PROJECT_ID`, `ZOHO_DC`, `ZOHO_MCP_*`) and a repo-root `.env` file are
still read as a last-resort fallback, with a warning on every command. Move them into the config file.

## CLI quick reference

Output is TOON by default; add `--json` for JSON. Every answer ends with a `help[]` block of the next commands worth
running. Unknown flags fail with exit code 2 and a "did you mean" hint, and nothing ever prompts. Run
`zoho-projects-axi <command> --help` for flags and examples (this table is checked against the command registry by the tests).

| Command | What it does |
|---|---|
| `zoho-projects-axi` / `zoho-projects-axi home` | live overview of the default project |
| `zoho-projects-axi project` | project overview: status, owner, next milestone, counts (`--lists` adds tasklists and milestones) |
| `zoho-projects-axi project list` | projects of the portal (id, name, status) |
| `zoho-projects-axi project use <name or id>` | make a project the default (writes the config file) |
| `zoho-projects-axi people` | portal users and tags (`--users`, `--tags`, `--limit N`) |
| `zoho-projects-axi tasks` | task list with totals by status (`--tasklist`, `--milestone`, `--status`, `--owner`, `--limit`, `--page`) |
| `zoho-projects-axi task <KEY or id>` | one task; slices `--comments`, `--history`, `--subtasks`, `--attachments`, `--images` |
| `zoho-projects-axi bug <KEY or id>` | one bug; slices `--comments`, `--attachments`, `--images`, `--task` (linked task) |
| `zoho-projects-axi resolve <text or ->` | find task/bug keys, ids and Zoho links in free text, offline; `--check` confirms them |
| `zoho-projects-axi auth status` | config and credential check, verifies the token with Zoho (`--offline` skips the network) |
| `zoho-projects-axi auth login` | sign in with your browser (OAuth); saves the refresh token to the config file (`--port N`, `--no-browser`) |
| `zoho-projects-axi auth login-help` | the one-time client registration steps and where the values go |
| `zoho-projects-axi cache status` | cache size, entry counts, oldest entry |
| `zoho-projects-axi cache clear` | delete the cache (needs `--yes`) |
| `zoho-projects-axi hooks install` / `zoho-projects-axi hooks uninstall` | add or remove the Claude Code SessionStart hook |
| `zoho-projects-axi skill install` | install the Claude Code skill |
| `zoho-projects-axi session-start` | the hook entrypoint (plain output, always exit 0) |
| `zoho-projects-axi mcp` | run the MCP server over stdio |

Global flags, accepted before or after the command: `--project P` (`PROJECT` or `PORTAL:PROJECT`), `--fresh` (bypass
the cache), `--json`, `--full` (every field, no truncation) and `--debug` (prints the Zoho request count to stderr).

Examples:

```bash
zoho-projects-axi tasks --status open --owner asha --limit 10
zoho-projects-axi task PROJ1-T1000 --comments --history
zoho-projects-axi bug PROJ1-I12 --comments --images
zoho-projects-axi task PROJ1-T1000 --json
zoho-projects-axi resolve "why does PROJ1-T123 loop? see AD1-I45"
zoho-projects-axi project --project "Demo Site" --lists
zoho-projects-axi task 4000000012345 --fresh --debug
```

Exit codes: `0` ok, `2` usage error, `3` auth or config problem, `4` not found, `5` Zoho unreachable or failing,
`6` rate limited, `1` anything else. Errors are three lines on stdout (`error`, `message`, `hint`).

## Use it from Claude (MCP)

The MCP server exposes six tools: `project`, `tasks`, `task`, `bug`, `people` and `resolve`. Each is the CLI command
of the same name, with the same arguments, plus `project`, `fresh`, `full` and `format` (`toon` or `json`).
`images=true` on `task` or `bug` returns images inline. Configuration comes from the config file, so the server
needs nothing but the command to start.

The easy way is the `zoho-projects-axi-mcp` plugin (see [Install](#install)); it runs the server through `uvx`. The
manual registrations below are for a tool-installed executable. The server reads the config when it starts, so after
your first `auth login` reconnect it (`/mcp` in Claude Code) or restart the client.

### Claude Code

```bash
claude mcp add --scope user zoho -- /absolute/path/to/zoho-projects-axi mcp
```

Use the **absolute path** of the executable (`command -v zoho-projects-axi`; for `uv tool` it is usually
`~/.local/bin/zoho-projects-axi`). A client launches the server from its own environment, which may not have your shell's
`PATH`, and a path into a project virtualenv that later moves or is deleted is exactly what silently killed the
0.1.x registration. A tool-installed executable does not depend on any checkout. Then run `claude mcp list` and
check that `zoho` is connected. The tools appear as `mcp__zoho__task`, `mcp__zoho__tasks` and so on; the name you
pass to `claude mcp add` is the prefix.

### Claude Desktop

Add the server to `claude_desktop_config.json` (on macOS usually
`~/Library/Application Support/Claude/claude_desktop_config.json`) and restart the app:

```json
{
  "mcpServers": {
    "zoho": {
      "command": "/absolute/path/to/zoho-projects-axi",
      "args": ["mcp"]
    }
  }
}
```

Do not put secrets in this JSON; the config file already holds them.

### Notes

- The MCP server has no tool to list or switch projects. Choose the default with the CLI: `zoho-projects-axi project list`,
  then `zoho-projects-axi project use "<name>"`. Any tool call can also pass `project=` for one call.
- Older registrations that run `python -m zoho_mcp` or `zoho-projects-mcp` no longer exist (removed in 0.2.0);
  re-register with `zoho-projects-axi mcp`.
- Stdout is the protocol wire. The server logs to stderr (and `log_file`, if set), redacting credentials.

## Claude Code hook and skill

The `zoho-projects-axi-skill` plugin already includes both, so you only need these commands if you installed the CLI
without the plugin. Both commands print a diff first and write nothing without `--yes`. They only touch `~/.claude`.

```bash
zoho-projects-axi hooks install
zoho-projects-axi hooks install --yes
zoho-projects-axi skill install
zoho-projects-axi skill install --yes
```

- **Hook.** Adds a `SessionStart` entry to `~/.claude/settings.json` that runs `zoho-projects-axi session-start`: a
  cache-only snapshot of the default project (about 83 tokens), silent on any failure. The previous file is copied to
  `settings.json.bak`. `zoho-projects-axi hooks uninstall --yes` removes it. The hook runs the bare command `zoho-projects-axi`, so it
  must be on the `PATH` that Claude Code sees.
- **Skill.** Copies the packaged skill to `~/.claude/skills/zoho-projects-axi/SKILL.md`; it teaches an agent to start with
  `resolve`, ask for slices and not paste credentials.

## Cache

Responses are cached in SQLite at `~/.cache/zoho-projects-axi/cache.sqlite` (`$XDG_CACHE_HOME/zoho-projects-axi/`), so repeated commands
cost no Zoho requests. Lifetimes are set by the `ttl_*` settings (portals, projects, users and tags 24 h; tasks and
bugs 5 min; comments and attachments 2 min by default) and applied when an entry is read, so changing one takes
effect immediately. Errors and downloads are never cached; a corrupt cache degrades to "no cache" with a warning.

```bash
zoho-projects-axi cache status
zoho-projects-axi cache clear --yes
zoho-projects-axi task PROJ1-T1000 --fresh
```

`--fresh` skips the cache for one call (and still refreshes it). `cache clear` also deletes the derived name index
and needs `--yes`. Images saved by `--images` go to `~/.cache/zoho-projects-axi/attachments/`.

## Troubleshooting

- **`auth.missing` or `config.project_missing` (exit 3).** Run `zoho-projects-axi auth status --offline` to see what is unset
  and where each value comes from, then `zoho-projects-axi auth login-help`. Environment variables override the file, which is
  a common reason a changed value seems ignored.
- **Token problems.** `zoho-projects-axi auth status` verifies the token online: `valid`, `invalid` (revoked or wrong
  credentials; run `zoho-projects-axi auth login` again), or `unreachable` (network, or a wrong `dc`). A
  refresh token can mint only a limited number of access tokens per ten minutes, so do not loop on a failing login.
- **Config permissions warning.** `... is readable by other users; run chmod 600 ...`. Run
  `chmod 600 ~/.config/zoho-projects-axi/config.toml`.
- **Wrong data center.** `dc` must be `com`, `eu`, `in`, `com.au` or `jp`; anything else is rejected before any
  request. A token from one data center does not work on another.
- **`rate_limited.client` (exit 6).** The built-in limiter refuses beyond 100 requests per 2 minutes so Zoho never
  locks the API (Zoho's documented lockout is 30 minutes). Wait the number of seconds in the hint. Cached and narrower
  commands use fewer requests.
- **`rate_limited.zoho` (exit 6).** Zoho answered 429 three times in a row (after 1 s and 2 s back-offs). Wait several
  minutes before retrying; repeated attempts extend the lockout.
- **Stale data.** Cached answers can be up to their TTL old. Use `--fresh` on one call, lower the `ttl_*` values, or
  `zoho-projects-axi cache clear --yes`. The session-start hook deliberately serves a snapshot up to four hours old; run
  `zoho-projects-axi` for live numbers.
- **A project is "not found" or the key does not resolve.** Key lookup needs the search scope; check
  `zoho-projects-axi project list`, pass `--project`, and see the unverified items below.
- **MCP shows no `project list` or `project use`.** By design: use the CLI (`zoho-projects-axi project use ...`), which writes the
  config file the server reads.
- **Login problems.** `auth.login_timeout`: no browser approval within 5 minutes, run `auth login` again.
  `usage.port_unavailable`: pick another port with `--port N` and register that redirect URI. `auth.state_mismatch` or
  `auth.exchange_failed`: use only the URL `auth login` printed; for a client of your own, check the redirect URI
  matches exactly and that the client is enabled for your data center. `auth.missing`: no client is configured, see
  `auth login-help`. No browser (ssh)? Run `auth login --no-browser` and open the URL on your machine; the redirect
  must reach the same host, so use a Self Client there instead.
- **`upstream.unreachable ... SSLError` (CERTIFICATE_VERIFY_FAILED).** Your network re-signs HTTPS traffic (Zscaler or
  another corporate proxy), so `curl` works but a bundled-certificate Python does not. zoho-projects-axi verifies
  against the operating system's certificate store (via `truststore`), which fixes this on current releases. On an
  older build, or if it still fails, point `SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE` at your organization's root
  certificate (PEM) and retry. The error names the cause (`SSLError`, `ConnectTimeout`, ...), never the URL.
- **Plugin cannot start (the launcher says it needs uv).** Install [uv](https://docs.astral.sh/uv/) (`brew install uv`)
  and restart Claude Code. The first run builds the package, so it takes a few seconds; later runs use uv's cache.
- **MCP tools say "Not signed in" right after login.** The server started before the token existed: reconnect it
  with `/mcp` or restart Claude Code.
- **MCP server will not connect.** Run `zoho-projects-axi mcp` in a terminal: it should wait silently for input (stop with
  Ctrl-C). Make sure the registered command is an absolute path to an executable that still exists.
- **`--debug`** prints the number of Zoho requests a command made to stderr.

## Testing

```bash
uv venv venv && uv pip install --python venv/bin/python -e ".[dev]"
venv/bin/python -m pytest tests -q
uvx --quiet mypy zoho_projects_axi --ignore-missing-imports --python-version 3.10
```

The default suite is offline: sockets are blocked and `ZOHO_*` variables are stripped per test. Its token budgets,
goldens and fixtures are described in [tests/README.md](tests/README.md).

**Live smoke (manual, never in the default suite).** With real credentials in place:

```bash
ZOHO_PROJECTS_AXI_LIVE=1 venv/bin/python -m pytest tests/live -q
```

Tests marked `live` are skipped without the switch; all their calls are GETs through the gateway.

## Known limitations and what is unverified

Be careful before relying on any of the following. This project was built **without access to a live Zoho
account**, so:

- **Every test fixture is synthetic.** Bodies follow Zoho's published response shapes and what the earlier 0.1.x code
  depended on, but values are made up. Each fixture file records, per field group, whether it is `documented`,
  `proven-by-legacy-code`, `invented` or `recorded`; none is `recorded` yet. A green test suite proves the code
  handles those shapes, not that Zoho sends them.
- **No end-to-end run against Zoho.** `zoho-projects-axi task <KEY>` was exercised only against replayed fixtures. I could not run
  `claude mcp list` or register the server in a real Claude installation here; the MCP server was verified over stdio
  by an MCP client in the test suite and a handshake in a throwaway environment (initialize and `tools/list`), not
  inside Claude Code or Claude Desktop. The `claude mcp add` and Claude Desktop snippets follow those tools' documented
  forms but were not executed.
- **`uv tool install` and `pipx` were not run** (only documented here); the package was built into a wheel and installed
  into a throwaway virtualenv, which is the same artifact those tools install.

Zoho facts that are still unconfirmed (details in [tests/README.md](tests/README.md)):

| Question | What the code does meanwhile |
|---|---|
| Zoho's v3 docs call bugs `/issues` and milestones `/phases`; the code uses `/bugs` and `/milestones` because 0.1.x did | tries v3 first and falls back to the legacy `/restapi` path on 404; milestone failure only becomes a warning |
| Does a bug detail carry `associated_task`? v3 documents a separate associated-tasks call instead | `bug --task` reads `associated_task` and is labelled unverified |
| Search result wrappers for bugs: `bugs`/`key` (as 0.1.x assumed) versus `issue`/`prefix` (as the legacy docs show); v3 documents a third shape (`results[]`, `entity_id`) | `resolve --check` and key lookup accept the known wrappers, and a key that does not resolve is reported as unresolved |
| `page_info` differs between endpoints: `has_next_page` is missing for bug lists and bare arrays appear for projects and status history | parsers accept both wrapped and bare shapes and use only `has_next_page` when it exists |
| The body of a throttle (429) reply, and of token-endpoint errors, is undocumented | 429 is detected by status code and retried; the token path reads an `error` string if present |
| Rate limit: v3 docs say 200 calls per 2 minutes per endpoint and a 10 minute block, legacy docs say 100 and 30 minutes | the client assumes the stricter 100 per 2 minutes |
| Search scope name: `ZohoProjects.search.READ` (used here) versus `ZohoSearch.securesearch.READ` (shown in Zoho's docs) | `auth login-help` asks for `ZohoProjects.search.READ`; if key resolution answers 401 or 403, add the other scope |
| Task attachment list shape on v3 (the docs show no sample); subtask listing (Zoho cannot filter by parent) | name, type and size are read from every known spelling; `--subtasks` is labelled unverified |

**How to settle them.** Record real, sanitized fixtures with the read-only recorder and commit the reviewed result:

```bash
python scripts/record_fixture.py task_comments/v3_404_legacy_ok \
    --call "task_comments portal=<id> project=<id> task=<id>" --literal "<portal or company name>"
```

It drives the real gateway (GET only), sanitizes ids, people, emails and tokens, refuses to write if it finds a
secret, and writes `tests/fixtures/zoho/<endpoint>/<name>.json`; use `--dry-run` first and read the output, since free
text is scrubbed by pattern only. Then run the live smoke (above) and update the provenance of the fixtures you replaced.

## Layout

- `zoho_projects_axi/core`: config, auth, HTTP gateway, cache, TOON encoder, render layer and the commands.
- `zoho_projects_axi/cli`: parser and registry (the single source of the command table, `--help` and the MCP tools).
- `zoho_projects_axi/mcp_server`: the MCP front-end.
- `zoho_projects_axi/skills`: the packaged Claude Code skill.
- `zoho_projects_axi/core/oauth_login.py`, `bundled_client.py`: the browser sign-in (PKCE, loopback redirect) and the
  slot for a shipped Zoho client.
- `.claude-plugin/marketplace.json`, `plugins/`: the two Claude Code plugins (`zoho-projects-axi-skill`,
  `zoho-projects-axi-mcp`), each a manifest plus a small `bin/zoho-projects-axi` launcher that runs through `uvx`.
- `scripts/record_fixture.py`: the dev-only fixture recorder.

MIT licensed; see [LICENSE](LICENSE).
