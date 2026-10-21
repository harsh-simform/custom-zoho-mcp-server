"""Recording half of the fixture pipeline (dev-only): a transport wrapper that captures read-only
exchanges at the transport seam, and the writer that sanitizes and stores them.

Used by `scripts/record_fixture.py`; unit-tested with the replay adapter standing in for the network.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Mapping

from zoho_axi.core.transport import Transport, TransportResponse

from .sanitizer import SecretDetected, Sanitizer, detect_secrets

PROVENANCE_MARKERS = ("documented", "proven-by-legacy-code", "invented", "recorded")


class RecordingTransport:
    """Wraps a real transport at the seam and remembers every GET exchange.

    Only GET exchanges are kept: the single POST the gateway ever sends is the OAuth token refresh,
    whose request and response carry credentials and must never be recorded. Responses pass through
    unchanged, so the gateway behaves exactly as it does without the wrapper."""

    def __init__(self, inner: Transport):
        self._inner = inner
        self._exchanges: list[tuple[str, str, dict | None, TransportResponse]] = []

    def send(self, method, url, headers, params, timeout, *, data=None, max_bytes=None) -> TransportResponse:
        response = self._inner.send(method, url, headers, params, timeout, data=data, max_bytes=max_bytes)
        if str(method).upper() == "GET":
            self._exchanges.append(("GET", url, dict(params) if params else None, response))
        return response

    def entries(self) -> list[dict]:
        """Raw (not yet sanitized) fixture entries in the order the requests were made."""
        return [_entry(*exchange) for exchange in self._exchanges]


def _entry(method: str, url: str, params: dict | None, response: TransportResponse) -> dict:
    entry: dict[str, Any] = {"method": method, "url": url}
    if params:
        entry["params"] = params
    entry["status"] = response.status
    content_type = response.content_type or ""
    if response.body.strip():
        try:
            entry["json"] = json.loads(response.body)
        except ValueError:
            if content_type.lower().startswith("text/"):
                entry["body_text"] = response.body.decode("utf-8", errors="replace")
            else:
                entry["body_base64"] = base64.b64encode(response.body).decode()
            entry["content_type"] = content_type
    return entry


def build_fixture(
    entries: list[dict], *, groups: Mapping[str, str], sanitizer: Sanitizer, note: str | None = None
) -> dict:
    """The sanitized `{"_provenance": ..., "entries": [...]}` document for raw `entries`.

    Refuses (SecretDetected) when the RAW entries contain a credential: a read-only GET response must
    never carry one, so its presence means something is wrong upstream. The scrubbed copy is checked
    again as defense in depth."""
    kinds = detect_secrets(entries)
    if kinds:
        raise SecretDetected(kinds)
    clean = sanitizer.sanitize_entries(entries)
    kinds = detect_secrets(clean)
    if kinds:
        raise SecretDetected(kinds)
    provenance: dict[str, Any] = {"groups": dict(groups)}
    if note:
        provenance["note"] = note
    return {"_provenance": provenance, "entries": clean}


def write_fixture(
    path: Path,
    entries: list[dict],
    *,
    groups: Mapping[str, str],
    sanitizer: Sanitizer,
    note: str | None = None,
    force: bool = False,
) -> Path:
    """`build_fixture` then store it. Nothing is written and no directory is created on a refusal."""
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass force=True (--force) to replace it")
    document = build_fixture(entries, groups=groups, sanitizer=sanitizer, note=note)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
