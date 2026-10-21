"""Small TOON (Token-Oriented Object Notation) encoder: https://toonformat.dev

Vendored instead of depending on the official `toon_format` package so the output rules are pinned
here and covered by the official spec fixtures (tests/test_toon_conformance.py); a dependency would
add a runtime package for ~150 lines. Encode only: output is one-way, for the agent to read.
Delimiter is always a comma, indent is two spaces. Not implemented: the spec's optional compact
forms (keyed tabular headers, nested field groups); the list form emitted instead is valid TOON.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal

_SIMPLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_NUMERIC_LIKE = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_STRUCTURAL = set(':"\\[]{},')

# (relative depth, text) pairs; joined with two spaces per depth level at the end.
_Lines = list[tuple[int, str]]


def encode(value) -> str:
    if isinstance(value, dict):
        lines = _object_lines(value)
    elif isinstance(value, (list, tuple)):
        lines = [(0, "[]")] if not value else _array_lines("", value)
    elif isinstance(value, str) and value.startswith("﻿"):
        lines = [(0, _quote(value))]  # a leading BOM would be swallowed as an encoding marker
    else:
        lines = [(0, _scalar(value))]
    return "\n".join("  " * depth + text for depth, text in lines)


def _is_primitive(value) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _escape(ch: str) -> str:
    if ch in _ESCAPES:
        return _ESCAPES[ch]
    if ord(ch) < 0x20:
        return f"\\u{ord(ch):04x}"
    return ch


def _quote(text: str) -> str:
    return '"' + "".join(_escape(ch) for ch in text) + '"'


def _string(text: str) -> str:
    """Quote anything that could be mistaken for another type or that contains structure.
    Commas are always quoted (even outside arrays): costs a few bytes, never ambiguous."""
    if (
        text == ""
        or text[0] in " \t"
        or text[-1] in " \t"
        or text in ("true", "false", "null")
        or text[0] in "-#"
        or _NUMERIC_LIKE.fullmatch(text)
        or any(ch in _STRUCTURAL or ord(ch) < 0x20 for ch in text)
    ):
        return _quote(text)
    return text


def _key(key) -> str:
    if not isinstance(key, str):
        raise TypeError(f"TOON keys must be strings, got {type(key).__name__}: {key!r}")
    return key if _SIMPLE_KEY.fullmatch(key) else _quote(key)


def _scalar(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number(value)
    raise TypeError(f"TOON cannot encode {type(value).__name__}: {value!r}")


def _number(value: float) -> str:
    """Canonical decimal: no exponent, no trailing zeros, -0 is 0, NaN/Infinity are null."""
    if not math.isfinite(value):
        return "null"
    text = format(Decimal(repr(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _shift(lines: _Lines, by: int) -> _Lines:
    return [(depth + by, text) for depth, text in lines]


def _object_lines(obj: dict) -> _Lines:
    lines: _Lines = []
    for key, value in obj.items():
        lines += _field_lines(key, value)
    return lines


def _field_lines(key: str, value) -> _Lines:
    name = _key(key)
    if isinstance(value, dict):
        return [(0, f"{name}:")] + _shift(_object_lines(value), 1)
    if isinstance(value, (list, tuple)):
        return [(0, f"{name}: []")] if not value else _array_lines(name, value)
    return [(0, f"{name}: {_scalar(value)}")]


def _tabular_fields(items) -> list[str] | None:
    """Field names when every item is a non-empty object with the same keys and primitive values."""
    if not items or not all(isinstance(item, dict) and item for item in items):
        return None
    fields = list(items[0])
    for item in items:
        if set(item) != set(fields) or not all(_is_primitive(v) for v in item.values()):
            return None
    return fields


def _array_lines(prefix: str, items, allow_tabular: bool = True) -> _Lines:
    """Non-empty arrays only; each caller renders the empty array in its own context."""
    count = len(items)
    if all(_is_primitive(item) for item in items):
        return [(0, f"{prefix}[{count}]: " + ",".join(_scalar(item) for item in items))]
    fields = _tabular_fields(items) if allow_tabular else None
    if fields is not None:
        header = ",".join(_key(f) for f in fields)
        lines: _Lines = [(0, f"{prefix}[{count}]{{{header}}}:")]
        for item in items:
            lines.append((1, ",".join(_scalar(item[f]) for f in fields)))
        return lines
    lines = [(0, f"{prefix}[{count}]:")]
    for item in items:
        lines += _shift(_list_item_lines(item), 1)
    return lines


def _list_item_lines(item) -> _Lines:
    if isinstance(item, (list, tuple)):
        if not item:
            return [(0, "- [0]:")]
        inner = _array_lines("", item, allow_tabular=False)  # arrays nested directly in a list stay in list form
        return [(0, f"- {inner[0][1]}")] + inner[1:]
    if not isinstance(item, dict):
        return [(0, f"- {_scalar(item)}")]  # primitives; _scalar raises TypeError for anything else
    if not item:
        return [(0, "-")]
    inner = _object_lines(item)
    return [(0, f"- {inner[0][1]}")] + _shift(inner[1:], 1)
