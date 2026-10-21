# Test harness

The default suite is offline and credential-free: `conftest.py` blocks every non-loopback socket and strips
`ZOHO_*` variables from the environment for each test.

    venv/bin/python -m pytest tests -q

## Layers

- **Seams.** Product seam: run a command, get rendered text plus an exit code (PRD). Below it the gateway's
  transport seam has two adapters: real HTTP and fixture replay (`support/replay.py`). Leaf seams: TOON
  encoder, config loader, the sanitizer and the harness helpers below.
- **Fixtures** live in `tests/fixtures/zoho/<endpoint>/<name>.json`. A file is one recorded scenario:
  `{"_provenance": {...}, "entries": [{method, url, params?, status, json | body_text | body_base64, content_type?}]}`.
  `support/fixtures.py`: `replay_for("task_comments/v3_404_legacy_ok", ...)` returns a `ReplayTransport`
  serving the entries in order; anything unrecorded raises `UnrecordedRequest` naming the method, URL and
  params. Entries are matched on exact params and each is consumed once, so a command that makes an extra
  or different call fails loudly. To vary a scenario, copy the entry with `replay.add(...)` using
  `first_json(name)`. `make_gateway(tmp_path, replay)` (in `support/factories.py`) builds a gateway with a
  fixed token and no real sleeping. Canonical fake ids (`PORTAL`, `PROJECT`, `TASK`, `TASK_PARENT`, `BUG`,
  `BUG_NO_TASK`, `TASKLIST`, `MILESTONE`, `IDS`) are in `support/fixtures.py`.
- **Provenance.** Every fixture states, per field group, one of `documented` (Zoho's published docs),
  `proven-by-legacy-code` (behavior the legacy `zoho_mcp` code depends on), `invented` (plausible synthetic)
  or `recorded` (a real, sanitized recording). There is no Zoho access in this repo's CI or dev setup, so the
  committed set is synthetic: bodies follow the documented shapes, values are made up, and the sanitizer
  pipeline was applied so ids are the canonical fakes. Replace a file with a real recording when you can.
- **Known gaps** found while building the set: the legacy search wrapper `issue` for bug hits and the
  attachment shapes `attachment_details` and `file_info` are handled (T08, T09); which bug-hit wrapper Zoho
  really sends (`bugs`/`key` vs `issue`/`prefix`) and whether `associated_task` exists on a bug detail are
  still unverified. The v3 docs call bugs `issues` and milestones
  `phases`; the gateway keeps the legacy-proven `/bugs` and `/milestones` paths and the fixtures follow it.

## Recording real fixtures (dev-only, read-only)

    python scripts/record_fixture.py task_comments/v3_404_legacy_ok \
        --call "task_comments portal=<id> project=<id> task=<id>" --literal "<portal or company name>"

It drives the real gateway (GET only; the OAuth refresh POST is never recorded), sanitizes the exchanges
and writes the file. `--dry-run` prints instead of writing, `--force` overwrites. Downloads are recorded
with a stub body, never real bytes. The sanitizer (`support/sanitizer.py`) is deterministic and scrubs
people, emails, ids, urls and tokens; real ids named portal/project/task/bug/tasklist/milestone are pinned
to the canonical fakes. It refuses to write if it finds an access/refresh token or client secret. Free text
is scrubbed by pattern only: read the result before committing.

## Token budgets

`assert_token_budget(text, max_tokens, label="...")` (`support/tokens.py`) measures **rendered output**.
The gating estimator is a deterministic offline proxy, `ceil(len(text) / 4)`. It is not a tokenizer: it
under-counts text dense in punctuation, digits, long ids or non-ASCII (typical of TOON rows), so leave
headroom and treat budgets as regression guards. A failure prints `measured N > allowed M`. Declare each
budget next to the tests of the command it guards; the run summary lists every measured/allowed pair.
`pip install -e ".[tokens]"` adds `tiktoken`, used only to add its count to failure messages; no test
depends on it.

## Goldens

`assert_golden("task/default", text)` compares with `tests/golden/task/default.txt` (diff on mismatch).
`pytest --update-goldens` rewrites goldens from current output: use it only for deliberate format changes
and review the diff. A missing golden fails until created that way.

## Live smoke (manual, never in the default suite)

    ZOHO_PROJECTS_AXI_LIVE=1 venv/bin/python -m pytest tests/live -q

Needs real credentials. Tests marked `live` are skipped without the switch, and with it the offline guard is
lifted for those tests only. All calls are GETs through the gateway.
