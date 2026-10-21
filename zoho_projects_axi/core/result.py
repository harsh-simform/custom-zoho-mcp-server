"""The data a command returns. Commands build a `Result`; they never format strings (see render.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple


@dataclass(frozen=True)
class Hint:
    """A suggested next call, as data. `params` is an ordered mapping of parameter name (python
    style, underscores) to value; `positional` names the params a CLI passes without a flag.
    Booleans become bare flags (`True`) or are dropped (`False`/`None`)."""

    command: str
    params: Mapping[str, Any] = field(default_factory=dict)
    positional: tuple[str, ...] = ()


@dataclass(frozen=True)
class Truncation:
    """A list cut short by the command (page size, --limit): `omitted` rows of the list named `label`
    are not shown. `expand` holds the options that fetch more, e.g. {"page": 2} -> `--page 2` /
    `page=2`. Over-long text is cut by the renderer itself and carries its hint inline."""

    label: str
    omitted: int
    unit: str = "rows"
    expand: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    """What every command returns.

    entity      key into schemas.SCHEMAS (what the payload is)
    payload     dict -> a detail view; list of dicts -> a list view. Keys the entity schema declares
                as sections (e.g. `comments` on a task) are rendered as tables after the fields.
    counts      aggregates (totals, status tallies) rendered above the data
    warnings    partial-failure / caveat lines; the command still succeeded (exit 0)
    help        suggested next calls
    truncations list caps applied by the command (the renderer adds its own text cuts)
    """

    entity: str
    payload: dict | list
    counts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    help: list[Hint] = field(default_factory=list)
    truncations: list[Truncation] = field(default_factory=list)


class Rendered(NamedTuple):
    """Output of the render layer: stdout text (no trailing newline) and the process exit code."""

    text: str
    exit_code: int
