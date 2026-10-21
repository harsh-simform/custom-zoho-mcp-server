#!/usr/bin/env python
"""Dev-only, READ-ONLY fixture recorder.

Calls the real Zoho API through `ZohoGateway` (so the read-only guard, limiter, retry and v3 -> legacy
fallback all apply), captures each GET exchange at the transport seam, runs the deterministic
sanitizer over it and stores the result as `tests/fixtures/zoho/<endpoint>/<name>.json`.
It refuses to write if a credential is detected, and never records the OAuth token exchange.

    python scripts/record_fixture.py task_comments/v3_404_legacy_ok \\
        --call "task_comments portal=<id> project=<id> task=<id>"

    --call "RESOURCE k=v ... ?param=value"  a gateway resource with its ids, then query params (repeatable)
    --download URL                           a file download, recorded with a stub body (repeatable)
    --group "name=marker: detail"            provenance of a field group (default: all=recorded: ...)
    --literal TEXT                           extra string to scrub everywhere (e.g. a company name)
    --mask-text KEY                          replace free text under KEY with same-length filler
    --dry-run                                print the sanitized fixture instead of writing it
    --force                                  overwrite an existing fixture

Real ids named portal/project/task/bug/tasklist/milestone are pinned to the canonical fake ids in
tests/support/fixtures.py so tests can refer to them. Needs the usual zoho-axi credentials; it makes no
request that is not a GET. Unit-tested with the replay adapter only; running it live is a manual step.
Review the output before committing: free text is scrubbed by pattern, not understood.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from support.fixtures import CANONICAL_IDS, FIXTURE_ROOT  # noqa: E402
from support.recording import RecordingTransport, build_fixture, write_fixture  # noqa: E402
from support.sanitizer import SecretDetected, Sanitizer  # noqa: E402

from zoho_axi.core.config import load_settings  # noqa: E402
from zoho_axi.core.errors import ZohoAxiError  # noqa: E402
from zoho_axi.core.gateway import ZohoGateway  # noqa: E402
from zoho_axi.core.render import render_error  # noqa: E402
from zoho_axi.core.resources import RESOURCES  # noqa: E402

DEFAULT_GROUPS = {"all": "recorded: live Zoho response, sanitized"}


class UsageProblem(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # never exit from inside argparse; main() maps this to exit 2
        raise UsageProblem(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="record_fixture.py", description="Record one sanitized, read-only Zoho fixture.")
    p.add_argument("name", help="<endpoint>/<name>, e.g. task_comments/v3_404_legacy_ok")
    p.add_argument("--call", action="append", default=[], metavar="SPEC")
    p.add_argument("--download", action="append", default=[], metavar="URL")
    p.add_argument("--group", action="append", default=[], metavar="NAME=MARKER: DETAIL")
    p.add_argument("--literal", action="append", default=[], metavar="TEXT")
    p.add_argument("--mask-text", action="append", default=[], metavar="KEY")
    p.add_argument("--note")
    p.add_argument("--fixtures-dir", default=str(FIXTURE_ROOT))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    return p


def _parse_call(spec: str) -> tuple[str, dict[str, str], dict[str, str]]:
    tokens = shlex.split(spec)
    if not tokens:
        raise UsageProblem("empty --call")
    resource, ids, params = tokens[0], {}, {}
    if resource not in RESOURCES:
        raise UsageProblem(f"unknown resource {resource!r}; known: {', '.join(sorted(RESOURCES))}")
    for token in tokens[1:]:
        target, text = (params, token[1:]) if token.startswith("?") else (ids, token)
        key, sep, value = text.partition("=")
        if not sep or not key:
            raise UsageProblem(f"expected k=v or ?k=v, got {token!r}")
        target[key] = value
    needed = RESOURCES[resource].id_names
    if set(ids) != needed:
        raise UsageProblem(f"resource {resource!r} needs ids {sorted(needed)}, got {sorted(ids)}")
    return resource, ids, params


def _parse_groups(items: Sequence[str]) -> dict[str, str]:
    groups = {}
    for item in items:
        key, sep, text = item.partition("=")
        if not sep or not key or not text:
            raise UsageProblem(f"--group expects NAME=MARKER: detail, got {item!r}")
        groups[key] = text
    return groups or dict(DEFAULT_GROUPS)


def _portal_literals(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """Portal and company names appear in many strings (urls, comments); learn them from a portals reply."""
    found: list[str] = []
    for entry in entries:
        body = entry.get("json")
        records = body.get("portals") if isinstance(body, dict) else None
        for record in records or []:
            if isinstance(record, dict):
                found += [v for k, v in record.items() if k in ("name", "company_name") and isinstance(v, str)]
    return found


def main(
    argv: Sequence[str] | None = None,
    *,
    transport=None,
    settings=None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    gateway_options: Mapping[str, Any] | None = None,
) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    try:
        args = _parser().parse_args(argv)
        endpoint, slash, _ = args.name.partition("/")
        if not slash or not endpoint or args.name.count("/") != 1 or ".." in args.name:
            raise UsageProblem("name must be <endpoint>/<name>")
        calls = [_parse_call(spec) for spec in args.call]
        if not calls and not args.download:
            raise UsageProblem("nothing to record: pass --call and/or --download")
        groups = _parse_groups(args.group)
    except UsageProblem as exc:
        print(f"usage error: {exc}", file=err)
        return 2

    pins: dict[str, str] = {}
    for _, ids, _ in calls:
        for name, real in ids.items():
            if name in CANONICAL_IDS and real not in pins:
                pins[real] = CANONICAL_IDS[name]

    if transport is None:
        from zoho_axi.core.http_transport import RequestsTransport

        transport = RequestsTransport()
    recorder = RecordingTransport(transport)
    try:
        settings = settings or load_settings()
        gateway = ZohoGateway(settings, transport=recorder, **dict(gateway_options or {}))
        for label, action in [
            *((resource, lambda r=resource, i=ids, p=params: gateway.get(r, params=p or None, **i))
              for resource, ids, params in calls),
            *(("download", lambda u=url: gateway.download(u)) for url in args.download),
        ]:
            seen = len(recorder.entries())
            try:
                action()
            except ZohoAxiError as exc:
                if len(recorder.entries()) == seen:  # nothing reached Zoho (bad config, refused URL): fatal
                    raise
                # a recorded 404/403/429 is a legitimate fixture
                print(f"note: {label} ended with {exc.code}; the exchange was still recorded", file=err)
    except ZohoAxiError as exc:
        print(render_error(exc).text, file=err)
        return exc.exit_code

    entries = recorder.entries()
    sanitizer = Sanitizer(
        literals=[*args.literal, *_portal_literals(entries)], pins=pins, mask_text_keys=args.mask_text
    )
    target = Path(args.fixtures_dir) / f"{args.name}.json"
    try:
        if args.dry_run:
            document = build_fixture(entries, groups=groups, sanitizer=sanitizer, note=args.note)
        else:
            write_fixture(target, entries, groups=groups, sanitizer=sanitizer, note=args.note, force=args.force)
    except SecretDetected as exc:
        print(f"refused: {exc}", file=err)
        return 1
    except FileExistsError as exc:
        print(f"refused: {exc}", file=err)
        return 1
    if args.dry_run:
        print(json.dumps(document, indent=2, ensure_ascii=False), file=out)
    else:
        print(f"wrote {target} ({len(entries)} exchange(s))", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
