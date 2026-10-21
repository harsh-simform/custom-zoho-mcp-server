"""The render layer: turns a `Result` (or an exception) into the text a front-end prints.

Public interface (consumed by the CLI T06, the MCP front-end T13 and the command modules T08-T11):

    render(result, mode="cli", full=False, fmt="toon") -> Rendered(text, exit_code)
    render_error(exc, mode="cli", fmt="toon")          -> Rendered(text, exit_code)

* `mode` is "cli" or "mcp". It only changes how next-step hints and the expand flag are spelled
  (`zoho-axi task KEY --comments` / `--full` vs `task ref=KEY comments=true` / `full=true`).
* `fmt` is "toon" (default) or "json" (`--json`, `format=json`). Both encodings are produced from the
  same shaped document, so they carry identical information; only the encoding differs.
* `full=True` shows every field of each entity and never truncates text; otherwise the minimal schema
  from schemas.py applies and text longer than the limit is cut with a size hint.
* `Rendered.text` has no trailing newline (the front-end adds it); `exit_code` follows the table in
  00-overview (0 for every `render`, including an explicit empty result).

This is the only module that imports `toon`; commands and front-ends hand it data and never format
strings (tests/test_render_boundary.py enforces that).
"""
from __future__ import annotations

import json
import re
import shlex
from html.parser import HTMLParser
from typing import Any, Mapping

from . import toon
from .errors import ZohoAxiError
from .result import Hint, Rendered, Result, Truncation
from .schemas import Schema, schema_for

DETAIL_TEXT_LIMIT = 1500
ROW_TEXT_LIMIT = 300
_EXPAND = {"cli": "--full", "mcp": "full=true"}


def render(result: Result, mode: str = "cli", full: bool = False, fmt: str = "toon") -> Rendered:
    _check(mode, fmt)
    doc = _document(result, mode, full)
    return Rendered(_encode(doc, fmt), 0)


def render_error(exc: BaseException, mode: str = "cli", fmt: str = "toon") -> Rendered:
    """`error` (stable code), `message` and, when there is one, `hint`; exit code from the exception.
    Anything that is not a ZohoAxiError becomes `internal_error` / exit 1 (message only, no traceback)."""
    _check(mode, fmt)
    if isinstance(exc, ZohoAxiError):
        doc = {"error": exc.code, "message": exc.message}
        if exc.hint:
            doc["hint"] = exc.hint
        return Rendered(_encode(doc, fmt), exc.exit_code)
    doc = {"error": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
    return Rendered(_encode(doc, fmt), 1)


def _check(mode: str, fmt: str) -> None:
    if mode not in _EXPAND:
        raise ValueError(f"mode must be 'cli' or 'mcp', got {mode!r}")
    if fmt not in ("toon", "json"):
        raise ValueError(f"fmt must be 'toon' or 'json', got {fmt!r}")


# --- document: the single shaped structure both encodings are produced from ---------------------

def _document(result: Result, mode: str, full: bool) -> dict:
    schema = schema_for(result.entity)
    payload = result.payload
    shaper = _Shaper(mode, full, schema.text_limit or DETAIL_TEXT_LIMIT)
    doc: dict[str, Any] = {}

    if isinstance(payload, dict):
        _detail(doc, payload, schema, result.counts, shaper)
    else:
        if result.counts:
            doc["counts"] = dict(result.counts)
        doc[schema.plural] = shaper.rows(payload, schema)
    if result.truncations:
        doc["truncated"] = [_format_truncation(t, mode) for t in result.truncations]
    if result.warnings:
        doc["warnings"] = list(result.warnings)
    if result.help:
        doc["help"] = [_format_hint(h, mode) for h in result.help]
    return doc


def _detail(doc: dict, payload: dict, schema: Schema, counts: dict, shaper: "_Shaper") -> None:
    sections = set(schema.sections)
    if schema.id_field and payload.get(schema.id_field) not in (None, ""):
        doc[schema.name] = payload[schema.id_field]
    short = list(schema.detail_fields)
    if shaper.full:  # every other field the gateway mapped, after the minimal ones
        skip = {schema.id_field, *schema.detail_fields, *schema.text_fields, *sections}
        short += [name for name in payload if name not in skip]
    for name in short:
        _put(doc, name, shaper.detail_value(payload.get(name)))
    if counts:
        doc["counts"] = dict(counts)
    for name in schema.text_fields:
        _put(doc, name, shaper.detail_value(payload.get(name)))
    for name, entity in schema.sections.items():
        if name in payload:
            doc[name] = shaper.rows(payload[name], schema_for(entity))


def _put(doc: dict, name: str, value: Any) -> None:
    if value not in (None, ""):
        doc[name] = value


class _Shaper:
    """Applies the minimal schema, HTML stripping and truncation for one render call."""

    def __init__(self, mode: str, full: bool, detail_limit: int = DETAIL_TEXT_LIMIT):
        self.mode = mode
        self.full = full
        self.detail_limit = detail_limit

    def rows(self, rows: list, schema: Schema) -> list[dict]:
        if self.full:  # every field any row has, in first-seen order, so the rows stay tabular
            names = list(dict.fromkeys(name for row in rows for name in row))
        else:
            names = list(schema.list_fields)
        return [{n: self._text(row.get(n), ROW_TEXT_LIMIT, collapse=True) for n in names} for row in rows]

    def detail_value(self, value: Any) -> Any:
        return self._text(value, self.detail_limit, collapse=False)

    def _text(self, value: Any, limit: int, collapse: bool) -> Any:
        if not isinstance(value, str):
            return value
        text = html_to_text(value)
        if collapse:
            text = " ".join(text.split())
        if self.full or len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()} ... (+{_size(len(text) - limit)} chars, {_EXPAND[self.mode]})"


def _size(n: int) -> str:
    """420 -> '420', 1800 -> '1.8k', 12345 -> '12k'. Truncates, never rounds up."""
    if n < 1000:
        return str(n)
    if n < 10000:
        tenths = n // 100
        return f"{tenths // 10}.{tenths % 10}k"
    return f"{n // 1000}k"


# --- HTML -> text --------------------------------------------------------------------------------

_MARKUP = re.compile(r"<[A-Za-z/!][^>]*>|&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);")
_BLOCK = {"p", "div", "br", "tr", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
          "blockquote"}
_SKIP = {"script", "style"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    """Plain text from Zoho's HTML snippets: tags dropped, entities decoded, block elements and list
    items on their own lines, runs of whitespace collapsed. Strings without markup pass through."""
    if not _MARKUP.search(value):
        return value
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    lines = (" ".join(line.replace("\xa0", " ").split()) for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)


# --- hints -------------------------------------------------------------------------------------

def _format_hint(hint: Hint, mode: str) -> str:
    if mode == "mcp":  # a tool-call hint: every param named
        return " ".join([hint.command, *_options(hint.params, mode)])
    positional = [shlex.quote(str(hint.params[name])) for name in hint.positional]
    rest = {k: v for k, v in hint.params.items() if k not in hint.positional}
    return " ".join(["zoho-axi", hint.command, *positional, *_options(rest, mode)])


def _options(params: Mapping[str, Any], mode: str) -> list[str]:
    out = []
    for name, value in params.items():
        if value is None or value is False:
            continue
        if mode == "mcp":
            out.append(f"{name}={'true' if value is True else shlex.quote(str(value))}")
        else:
            flag = "--" + name.replace("_", "-")
            out += [flag] if value is True else [flag, shlex.quote(str(value))]
    return out


def _format_truncation(t: Truncation, mode: str) -> str:
    expand = " ".join(_options(t.expand, mode))
    return f"{t.label} (+{t.omitted} {t.unit}{', ' + expand if expand else ''})"


# --- encoding ----------------------------------------------------------------------------------

def _encode(doc: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    return toon.encode(_toon_view(doc))


def _toon_view(doc: dict) -> dict:
    view: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "help":
            view[key] = [{"run": cmd} for cmd in value]
        elif key == "warnings":
            view[key] = [{"message": w} for w in value]
        elif key == "counts":
            view[key] = " ".join(f"{k}={v}" for k, v in value.items())
        elif value == []:
            view[key] = "0 results"
        else:
            view[key] = value
    return view
