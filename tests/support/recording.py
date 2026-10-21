"""Recording half of the fixture pipeline (dev-only): a transport wrapper that captures read-only
exchanges at the transport seam, and the writer that sanitizes and stores them.

Used by `scripts/record_fixture.py`; unit-tested with the replay adapter standing in for the network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .sanitizer import SecretDetected, Sanitizer, detect_secrets

PROVENANCE_MARKERS = ("documented", "proven-by-legacy-code", "invented", "recorded")


def write_fixture(
    path: Path,
    entries: list[dict],
    *,
    groups: Mapping[str, str],
    sanitizer: Sanitizer,
    note: str | None = None,
    force: bool = False,
) -> Path:
    """Sanitize `entries` and store them as `{"_provenance": ..., "entries": [...]}`.

    Refuses (SecretDetected, nothing written, no directory created) when the RAW entries contain a
    credential: a read-only GET response must never carry one, so its presence means something is
    wrong upstream. The scrubbed copy is checked again as defense in depth."""
    path = Path(path)
    kinds = detect_secrets(entries)
    if kinds:
        raise SecretDetected(kinds)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass force=True (--force) to replace it")
    clean = sanitizer.sanitize_entries(entries)
    kinds = detect_secrets(clean)
    if kinds:
        raise SecretDetected(kinds)
    provenance: dict[str, Any] = {"groups": dict(groups)}
    if note:
        provenance["note"] = note
    document = {"_provenance": provenance, "entries": clean}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
