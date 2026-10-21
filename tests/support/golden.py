"""Golden-output helper: compare rendered text to a checked-in file, and rewrite it deliberately.

    assert_golden("task/default", text)       # tests/golden/task/default.txt
    pytest --update-goldens                   # rewrite goldens from the current output

Goldens are only ever written under an explicit ``--update-goldens`` run, so an accidental format
change shows up as a failing test with a diff, never as a silently "fixed" file. A missing golden is a
failure too (with the same hint), so a new golden is always a deliberate, reviewed addition.
Trailing newlines are not significant; the file is stored with exactly one.
"""
from __future__ import annotations

import difflib
from pathlib import Path, PurePosixPath

GOLDEN_ROOT = Path(__file__).resolve().parent.parent / "golden"

_update = False


def configure(update: bool) -> None:
    """Called once by tests/conftest.py with the value of ``--update-goldens``."""
    global _update
    _update = bool(update)


def _path_for(name: str, root: Path) -> Path:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError(f"golden name must be a relative path without '..': {name!r}")
    path = root / pure
    return path if path.suffix else path.with_name(path.name + ".txt")


def assert_golden(name: str, text: str, *, root: Path | None = None, update: bool | None = None) -> None:
    path = _path_for(name, root or GOLDEN_ROOT)
    actual = text.rstrip("\n") + "\n"
    if update if update is not None else _update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not path.exists():
        raise AssertionError(
            f"golden file missing: {path}\nCreate it deliberately with: pytest --update-goldens"
        )
    expected = path.read_text(encoding="utf-8").rstrip("\n") + "\n"
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"golden {name}",
                tofile="actual",
            )
        )
        raise AssertionError(
            f"output differs from golden {path}\n{diff}\n"
            "If the change is intended, update it with: pytest --update-goldens"
        )
