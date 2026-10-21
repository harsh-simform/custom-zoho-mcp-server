"""`zoho-axi resolve "<free text>" [--check]`: turn a pasted prompt into the entities it names and the exact
commands to run next (decision D5: a deterministic, LLM-free resolver, so the agent starts with the right call).

What it reads, all by regex over the text and nothing else:

* keys            `PROJ1-T123`, `AD1-B45`, `DM1-I9` (task `-T`, bug `-B` legacy / `-I` v3), case-insensitive. The
                  prefix is anything shaped like a Zoho project prefix (letters + digits) or one the derived
                  index already knows from keys it has seen (so an unusual prefix still matches).
* bare keys       `T1231`, `B45`: completed with the project's prefix when it is unambiguous (the index's, else
                  the one the text itself uses); otherwise reported unresolved, never guessed.
* "task 5"        `task|bug|issue|ticket <number>`: a short number is a sequence number (handled like a bare key),
                  a long one (>= 10 digits) is Zoho's internal id and works with `task <id>` as it is.
* Zoho URLs       `#taskdetail/<portal>/<project>/<id>`, `#buginfo/...`, `/projects/<p>/tasks|bugs|issues/<id>` on a
                  `*.zoho.<tld>` host; they carry ids, so they are always resolved and may name another project.

The text is DATA. Nothing from it is echoed except the matched keys and digits, every suggested command is
assembled from those validated tokens plus fixed words, and the text can never add a flag: `--full` or
`cache clear` inside a prompt stays prose. Keyword hints (a fixed table below) only choose which of a command's
own slices to suggest, and only slices the command table says the command has (`bug` has no `--history`).

Offline by default: the derived index (T07) is read, nothing is fetched, so a run costs 0 requests. With
`--check`, a key the index does not know is looked up through Search (one call each, then memoized by the same
code `task` uses); that is the only network use. `resolved: yes` means the id is known (a URL/long id, a key the
index remembers, or a key `--check` confirmed); `no` means unverified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from ..config import VALID_DATA_CENTERS
from ..errors import ConfigError, NotFoundError
from ..result import Hint, Result
from ._item import BUG, TASK, resolve_item
from ._scope import optional, scope
from .base import Context

MAX_TEXT = 100_000      # characters read (argv or stdin); the rest is ignored with a warning
MAX_LISTED = 5          # entities listed, and the most `--check` will look up
LONG_ID = 10            # digits from which a number is Zoho's internal id rather than a key's sequence number
EMPTY_NEXT = Hint("tasks", {"limit": 10})
_EMPTY_NEXT_TEXT = "zoho-axi tasks --limit 10"

_LETTER_KIND = {"T": "task", "B": "bug", "I": "bug"}
_ITEM_KIND = {"task": TASK, "bug": BUG}
_GENERIC_PREFIX = r"[A-Za-z]{1,8}\d{1,3}"  # Zoho project prefixes look like PROJ1, AD1, DM1, EC1

_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)
# `zoho.<dc>` or any subdomain of it, for a data center Zoho actually runs: `zoho.evil.com` is not Zoho's
_URL_HOST = re.compile(
    r"(?:^|\.)zoho\.(?:" + "|".join(re.escape(dc) for dc in VALID_DATA_CENTERS) + r")$", re.IGNORECASE)
_URL_IDS = (  # (pattern, kind word group, group order) over the whole URL, fragment included
    re.compile(r"(taskdetail|buginfo|bugdetail|issuedetail)/(\d+)/(\d+)/(\d+)", re.IGNORECASE),
    re.compile(r"(?:portal/(\d+)/)?projects/(\d+)/(tasks|bugs|issues)/(\d+)", re.IGNORECASE),
)
_BARE = re.compile(r"(?<![A-Za-z0-9_-])([TBI])(\d{1,9})(?![A-Za-z0-9_-])")  # uppercase only: "i18n", "t1" are not keys
_WORD_NUMBER = re.compile(
    r"\b(task|bug|issue|ticket)\s*(?:#|no\.?|number|id)?\s*:?\s*(\d{1,18})(?![\w]|[-.]\d)", re.IGNORECASE)

# keyword -> slice. A slice is only suggested for a command whose flag exists (see `_flags_for`).
_SLICE_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in (
    ("comments", r"\b(?:comments?|commented|said|discussion|discussed|replied|replies)\b"),
    ("history", r"\b(?:history|status changes?|status changed|changed status|moved)\b"),
    ("subtasks", r"\bsub-?\s?tasks?\b"),
    ("attachments", r"\b(?:attachments?|files?)\b"),
    ("images", r"\b(?:attached|screenshots?|screen shots?|images?|pictures?|photos?|png|jpe?g)\b"),
))
_MILESTONE_WORDS = re.compile(r"\b(?:milestones?|sprints?)\b", re.IGNORECASE)
_SLICE_ORDER = ("comments", "history", "subtasks", "attachments", "images")  # the order `task --help` lists them


@dataclass(frozen=True)
class _Mention:
    pos: int
    kind: str                  # "task" | "bug"
    prefix: str = ""           # upper-case project prefix of a full key
    letter: str = ""           # T / B / I as written; "" for "task 5"
    number: str = ""           # the key's sequence number
    ident: str = ""            # Zoho's internal id (URL, or a long number)
    portal: str = ""           # URL only
    project: str = ""          # URL only


@dataclass
class _Found:
    kind: str
    key: str                   # what is shown
    ref: str | None            # what the command takes; None when it cannot be built (unknown prefix)
    resolved: bool
    portal: str = ""
    project: str = ""


# --- scanning -----------------------------------------------------------------------------------------

def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _url_mentions(text: str) -> tuple[list[_Mention], list[tuple[int, int]]]:
    mentions, spans = [], []
    for match in _URL.finditer(text):
        url = match.group(0)
        host = url.split("/", 3)[2].split("@")[-1].split(":")[0] if url.count("/") >= 2 else ""
        if not _URL_HOST.search(host):
            continue
        spans.append(match.span())
        first, second = _URL_IDS
        if (m := first.search(url)):
            kind = "task" if m.group(1).lower() == "taskdetail" else "bug"
            mentions.append(_Mention(match.start(), kind, ident=m.group(4), portal=m.group(2), project=m.group(3)))
        elif (m := second.search(url)):
            kind = "task" if m.group(3).lower() == "tasks" else "bug"
            mentions.append(_Mention(match.start(), kind, ident=m.group(4), portal=m.group(1) or "", project=m.group(2)))
    return mentions, spans


def _key_pattern(known_prefixes: list[str]) -> re.Pattern[str]:
    alternatives = [re.escape(p) for p in sorted(known_prefixes, key=len, reverse=True)] + [_GENERIC_PREFIX]
    return re.compile(rf"(?<![A-Za-z0-9_])({'|'.join(alternatives)})-([TBI])(\d{{1,9}})(?![A-Za-z0-9_])", re.IGNORECASE)


def _mentions(text: str, known_prefixes: list[str]) -> list[_Mention]:
    found, spans = _url_mentions(text)
    rest = _mask(text, spans)
    key_spans = []
    for m in _key_pattern(known_prefixes).finditer(rest):
        letter = m.group(2).upper()
        found.append(_Mention(m.start(), _LETTER_KIND[letter], prefix=m.group(1).upper(), letter=letter, number=m.group(3)))
        key_spans.append(m.span())
    rest = _mask(rest, key_spans)
    bare_spans = []
    for m in _BARE.finditer(rest):
        found.append(_Mention(m.start(), _LETTER_KIND[m.group(1)], letter=m.group(1), number=m.group(2)))
        bare_spans.append(m.span())
    rest = _mask(rest, bare_spans)
    for m in _WORD_NUMBER.finditer(rest):
        kind = "task" if m.group(1).lower() in ("task", "ticket") else "bug"
        digits = m.group(2)
        found.append(_Mention(m.start(), kind, ident=digits) if len(digits) >= LONG_ID
                     else _Mention(m.start(), kind, number=digits))
    return sorted(found, key=lambda mention: mention.pos)


def _slices(text: str) -> set[str]:
    return {name for name, pattern in _SLICE_WORDS if pattern.search(text)}


# --- the index (local database only) -------------------------------------------------------------------

def _offline_scope(ctx: Context) -> tuple[str, str] | None:
    """(portal, project) from flags and config, without any lookup; None when unknown or only a name."""
    try:
        portal, project = ctx.resolve_project()
    except ConfigError:
        return None
    return (portal, project) if project.isdigit() else None


def _prefixes(keys: list[tuple[str, str]]) -> list[str]:
    return sorted({key.rsplit("-", 1)[0] for _, key in keys if "-" in key})


def _bug_letter(keys: list[tuple[str, str]], mentions: list[_Mention]) -> str:
    for kind, key in keys:
        if kind == "bug" and "-" in key:
            return key.rsplit("-", 1)[1][:1].upper()
    return next((m.letter for m in mentions if m.kind == "bug" and m.prefix), "")


# --- entities ------------------------------------------------------------------------------------------

def _entities(mentions: list[_Mention], keys: list[tuple[str, str]], index) -> tuple[list[_Found], list[str], list[str]]:
    """(entities in order of appearance, keys whose prefix is unknown, keys whose prefix is ambiguous)."""
    known = _prefixes(keys)
    seen_in_text = sorted({m.prefix for m in mentions if m.prefix})
    prefixes = known or seen_in_text
    prefix = prefixes[0] if len(prefixes) == 1 else ""
    bug_letter = _bug_letter(keys, mentions)

    def known_key(kind: str, key: str) -> bool:
        return index is not None and bool(index.resolve_key(key, kind=kind))

    out: list[_Found] = []
    unknown: list[str] = []
    ambiguous: list[str] = []
    seen: set[tuple[str, str]] = set()
    for m in mentions:
        if m.ident:
            found = _Found(m.kind, m.ident, m.ident, True, m.portal, m.project)
        elif m.prefix:
            key = f"{m.prefix}-{m.letter}{m.number}"
            found = _Found(m.kind, key, key, known_key(m.kind, key))
        elif prefix:
            letter = (bug_letter or m.letter or "I") if m.kind == "bug" else "T"
            key = f"{prefix}-{letter}{m.number}"
            found = _Found(m.kind, key, key, known_key(m.kind, key))
        else:
            shown = f"{m.letter}{m.number}" if m.letter else m.number
            found = _Found(m.kind, shown, None, False)
            (ambiguous if len(prefixes) > 1 else unknown).append(shown)
        if (found.kind, found.key) not in seen:
            seen.add((found.kind, found.key))
            out.append(found)
    return out, unknown, ambiguous


# --- suggestions ---------------------------------------------------------------------------------------

def _command_flags(ctx: Context) -> Mapping[str, frozenset[str]]:
    if ctx.command_flags is not None:
        return ctx.command_flags
    from ...cli.registry import REGISTRY  # only when a front-end built the Context without the table

    return {s.name: frozenset(f.name for f in s.flags) for s in REGISTRY}


def _flags_for(kind: str, wanted: set[str], supported: frozenset[str]) -> list[str]:
    """The slice params to suggest for `kind`, in the command's own order, limited to what it supports.
    `images` falls back to `attachments` until the command has `--images`."""
    chosen = set()
    for name in wanted:
        if f"--{name}" in supported:
            chosen.add(name)
        elif name == "images" and "--attachments" in supported:
            chosen.add("attachments")
    return [name for name in _SLICE_ORDER if name in chosen]


def _project_param(ctx: Context, found: _Found, scope_: tuple[str, str] | None) -> str | None:
    if found.project:
        if scope_ and found.project == scope_[1] and (not found.portal or found.portal == scope_[0]):
            return ctx.project
        return f"{found.portal}:{found.project}" if found.portal else found.project
    return ctx.project


def _milestone_hint(text: str, index) -> Hint:
    """`tasks --milestone NAME` when the text names a milestone the index knows (an exact command), else the
    command that lists the milestones with their ids."""
    lowered = text.casefold()
    names = sorted((n for n in (index.names("milestone") if index is not None else []) if len(n) >= 3),
                   key=len, reverse=True)
    for name in names:
        if name.casefold() in lowered:
            return Hint("tasks", {"milestone": name})
    return Hint("project", {"lists": True})


def resolve(ctx: Context) -> Result:
    raw = str(ctx.params["text"])
    text = ctx.read_stdin(MAX_TEXT + 1) if raw == "-" else raw
    warnings: list[str] = []
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT]
        warnings.append(f"text truncated to its first {MAX_TEXT} characters")

    scope_ = _offline_scope(ctx)
    index = ctx.data.index(portal=scope_[0], project=scope_[1]) if scope_ else None
    keys = index.known_keys() if index is not None else []
    mentions = _mentions(text, _prefixes(keys))
    found, unknown, ambiguous = _entities(mentions, keys, index)

    if len(found) > MAX_LISTED:
        warnings.append(f"{len(found) - MAX_LISTED} more found, not listed")
        found = found[:MAX_LISTED]
    if unknown:
        warnings.append(f"project prefix unknown for {' '.join(unknown)}; run `{_EMPTY_NEXT_TEXT}` to learn it, "
                        "or write the full key (e.g. PROJ1-T123)")
    if ambiguous:
        warnings.append(f"several project prefixes known for {' '.join(ambiguous)}; write the full key")

    if ctx.params.get("check"):
        _check(ctx, found, warnings)

    wanted = _slices(_mask(text, _url_mentions(text)[1]))
    supported = _command_flags(ctx)
    hints: list[Hint] = []
    for f in found:
        if f.ref is None:
            continue
        flags = {name: True for name in _flags_for(f.kind, wanted, supported.get(f.kind, frozenset()))}
        params: dict = {"ref": f.ref, **flags}
        if (project := _project_param(ctx, f, scope_)):
            params["project"] = project
        hints.append(Hint(f.kind, params, positional=("ref",)))
    if _MILESTONE_WORDS.search(text):
        hints.append(_milestone_hint(text, index))
    if not found or any(f.ref is None for f in found):
        hints.append(EMPTY_NEXT)

    rows = [{"key": f.key, "type": f.kind, "resolved": "yes" if f.resolved else "no"} for f in found]
    return Result("resolve", rows, warnings=warnings, help=hints)


def _check(ctx: Context, found: list[_Found], warnings: list[str]) -> None:
    """Look up each completed key the index does not know: one Search call, memoized. Ids are not looked up."""
    candidates = [f for f in found if f.ref and not f.resolved]
    if not candidates:
        return
    portal, project = scope(ctx)
    for f in candidates:
        def attempt(f: _Found = f):
            try:
                return resolve_item(ctx, _ITEM_KIND[f.kind], f.key, portal, project)
            except NotFoundError:
                warnings.append(f"{f.key} not found in project {project}")
                return None

        if optional(warnings, f"check {f.key}", attempt) is not None:
            f.resolved = True
