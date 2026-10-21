"""Deterministic fixture sanitizer: scrubs names, emails, ids, urls and tokens from recorded Zoho
responses before they are committed, and detects credentials so the recorder can refuse to write.

Rules (all stable across runs and machines: replacements derive from SHA-256 of the original plus a
fixed salt, never from random numbers, time or dict order):

* ids      keys named `id` / `*_id` / `*ids` / `zuid` / `zpuid` / `zp_id` holding a digit string of 5+
           chars (str or int) become a different number of the SAME length, so shapes survive; long
           alphanumeric ids (WorkDrive style) keep their length too. Digit runs of 8+ in free text and in
           URL path segments are mapped the same way, so one real id has exactly one fake id everywhere.
           `pins={real: fake}` forces canonical fake ids (tests name them). Values under keys that look
           like time/size/count/duration are left alone.
* people   a dict with `email`/`zpuid`/`zuid`/`first_name`/... (or sitting under created_by, owner, ...)
           is a person: its name fields become fake names from a fixed pool; the harvested names are
           also replaced inside free text (comments). Names of things that are not people are kept.
* emails   anywhere, case-insensitive -> `user-<8 hex>@example.test`.
* urls     scheme and Zoho hosts kept, other hosts -> `host-<hash>.example.test`, query/fragment/userinfo
           dropped (signed download URLs carry credentials-like blobs), id and portal-name path
           segments replaced.
* tokens   Zoho tokens, Bearer/JWT strings and values under access_token/refresh_token/client_secret-like
           keys are replaced. `detect_secrets` reports them (by kind, never by value) so a recording that
           contains one is refused, not committed.
* phones   masked. Free text can additionally be replaced with same-length filler via `mask_text_keys`.

Known limit: free text is scrubbed by pattern, so a name or customer detail that is neither a harvested
person, a literal you passed, an email, a url nor an id can survive. Review recordings before committing.
"""
from __future__ import annotations

import base64
import hashlib
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<redacted>"
REDACTED_TOKEN = "<redacted-token>"

# 1x1 transparent PNG: the stand-in for any downloaded image. Real bytes are never kept.
STUB_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
STUB_BYTES_BASE64 = base64.b64encode(b"synthetic-bytes").decode()

_FIRST_NAMES = ["Asha", "Ravi", "Meera", "Dev", "Nina", "Omar", "Lena", "Tariq", "Iris", "Kofi", "Sana", "Joel",
                "Mira", "Anil", "Zoe", "Hugo"]
_LAST_NAMES = ["K", "P", "S", "T", "Rao", "Vega", "Okoye", "Lind", "Marsh", "Hale", "Cole", "Nair", "Wren", "Shah",
               "Voss", "Ito"]
_LOREM = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et "
          "dolore magna aliqua enim minim veniam quis nostrud exercitation ullamco laboris nisi aliquip ex ea "
          "commodo consequat").split()

_PERSON_MARKER_KEYS = {"email", "zpuid", "zuid", "zp_id", "first_name", "last_name", "full_name"}
_PERSON_PARENT_KEYS = {
    "created_by", "updated_by", "modified_by", "last_modified_by", "added_by", "associated_by", "owner", "owners",
    "assignee", "reviewed_by", "reporting_to", "user", "reporter", "followers",
}
_NAME_KEYS = {"name", "full_name", "display_name", "user_name", "username"}
_ID_KEYS = {"id", "ids", "zuid", "zpuid", "zp_id"}
_PHONE_KEYS = ("phone", "mobile", "fax")
_NO_DIGIT_SCRUB_KEY_PARTS = ("millis", "size", "count", "epoch", "timestamp", "duration", "time", "sequence")
_SECRET_KEYS = {
    "access_token", "refresh_token", "client_secret", "id_token", "authorization", "api_key", "apikey", "password",
    "secret",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_DIGITS_RE = re.compile(r"(?<![0-9A-Za-z])\d{8,}(?![0-9A-Za-z])")
_ZOHO_HOST_RE = re.compile(r"(^|\.)(zoho|zohopublic|zohocdn|zohoprojects)\.(com|eu|in|jp|ca|sa|com\.au|com\.cn)$")

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("zoho-token", re.compile(r"1000\.[0-9a-fA-F]{32}\.[0-9a-fA-F]{32}")),
    ("bearer-token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=\-]{16,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{5,}")),
    ("secret-in-query", re.compile(r"(?i)(?:access_token|refresh_token|client_secret)=[^&\s]+")),
    ("client-secret-shaped", re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{40}(?![0-9A-Za-z])")),
)
_TOKEN_SCRUB_ORDER = ("zoho-token", "bearer-token", "jwt", "secret-in-query", "client-secret-shaped")


class SecretDetected(Exception):
    """A recording contains something that looks like a credential. The message lists the kinds of
    finding only, never the matching text."""

    def __init__(self, kinds: Iterable[str]):
        self.kinds = sorted(set(kinds))
        super().__init__("refusing to write fixture: found " + ", ".join(self.kinds))


def _norm_key(key: Any) -> str:
    return str(key).lower().replace("-", "_")


def detect_secrets(value: Any) -> list[str]:
    """Kinds of credential found in a decoded JSON value (keys and strings). Never returns values."""
    found: set[str] = set()

    def scan_text(text: str) -> None:
        for kind, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                found.add(kind)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                nkey = _norm_key(key)
                if nkey in _SECRET_KEYS and isinstance(item, str) and item and item not in (REDACTED, REDACTED_TOKEN):
                    found.add(f"key:{nkey}")
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            scan_text(node)

    walk(value)
    return sorted(found)


class Sanitizer:
    def __init__(
        self,
        *,
        literals: Iterable[str] = (),
        pins: Mapping[str, str] | None = None,
        mask_text_keys: Iterable[str] = (),
        salt: str = "zoho-axi-fixtures-v1",
    ):
        self._salt = salt
        self._literals = sorted({s for s in literals if s}, key=len, reverse=True)
        self._pins = dict(pins or {})
        self._mask_keys = {_norm_key(k) for k in mask_text_keys}
        self._names: dict[str, str] = {}  # real name (full or single token) -> fake

    # --- hashing helpers ---------------------------------------------------------------------

    def _hex(self, kind: str, value: str) -> str:
        return hashlib.sha256(f"{self._salt}|{kind}|{value}".encode()).hexdigest()

    def _fake_digits(self, real: str) -> str:
        if real in self._pins:
            return self._pins[real]
        digest, out = self._hex("id", real), ""
        while len(out) < len(real):
            out += "".join(str(int(c, 16) % 10) for c in digest)
            digest = hashlib.sha256(digest.encode()).hexdigest()
        out = out[: len(real)]
        return ("1" if out[0] == "0" else out[0]) + out[1:]

    def _fake_alnum(self, real: str) -> str:
        if real in self._pins:
            return self._pins[real]
        digest, out = self._hex("alnum", real), ""
        while len(out) < len(real):
            out += digest
            digest = hashlib.sha256(digest.encode()).hexdigest()
        return out[: len(real)]

    def _fake_first(self, real: str) -> str:
        return _FIRST_NAMES[int(self._hex("first", real)[:8], 16) % len(_FIRST_NAMES)]

    def _fake_last(self, real: str) -> str:
        return _LAST_NAMES[int(self._hex("last", real)[:8], 16) % len(_LAST_NAMES)]

    # --- people ------------------------------------------------------------------------------

    @staticmethod
    def _is_person(node: Mapping, parent_key: str | None) -> bool:
        return bool(_PERSON_MARKER_KEYS & {_norm_key(k) for k in node}) or (
            parent_key is not None and _norm_key(parent_key) in _PERSON_PARENT_KEYS
        )

    def _learn_person(self, node: Mapping) -> None:
        first = node.get("first_name") if isinstance(node.get("first_name"), str) else ""
        last = node.get("last_name") if isinstance(node.get("last_name"), str) else ""
        if len(first) >= 2:
            self._names.setdefault(first, self._fake_first(first))
        if len(last) >= 2:
            self._names.setdefault(last, self._fake_last(last))
        for key, value in node.items():
            if _norm_key(key) in _NAME_KEYS and isinstance(value, str) and value.strip():
                self._names.setdefault(value, self._compose_name(value, first, last))

    def _compose_name(self, full: str, first: str, last: str) -> str:
        tokens = full.split()
        out = []
        for index, token in enumerate(tokens):
            if first and token == first:
                out.append(self._fake_first(first))
            elif last and token == last:
                out.append(self._fake_last(last))
            else:
                out.append(self._fake_first(token) if index == 0 else self._fake_last(token))
        return " ".join(out)

    def _harvest(self, node: Any, parent_key: str | None = None) -> None:
        if isinstance(node, dict):
            if self._is_person(node, parent_key):
                self._learn_person(node)
            for key, item in node.items():
                self._harvest(item, str(key))
        elif isinstance(node, (list, tuple)):
            for item in node:
                self._harvest(item, parent_key)

    # --- strings -----------------------------------------------------------------------------

    def _fake_host(self, host: str) -> str:
        return host if _ZOHO_HOST_RE.search(host) else f"host-{self._hex('host', host)[:8]}.example.test"

    def sanitize_url(self, url: str) -> str:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            port = parts.port
        except ValueError:
            return "https://host-invalid.example.test/"
        netloc = self._fake_host(host) + (f":{port}" if port else "")
        segments = parts.path.split("/")
        for index, seg in enumerate(segments):
            if seg.isdigit() and len(seg) >= 5:
                segments[index] = self._fake_digits(seg)
            elif index > 0 and segments[index - 1] == "portal" and seg:
                segments[index] = f"portal-{self._hex('portal', seg)[:6]}"
            elif len(seg) >= 20 and re.fullmatch(r"[A-Za-z0-9_\-]+", seg) and any(c.isdigit() for c in seg):
                segments[index] = self._fake_alnum(seg)
        return self._apply_literals(urlunsplit((parts.scheme, netloc, "/".join(segments), "", "")))

    def _apply_literals(self, text: str) -> str:
        for literal in self._literals:
            text = re.sub(re.escape(literal), f"redacted-{self._hex('literal', literal.lower())[:4]}", text,
                          flags=re.IGNORECASE)
        return text

    def _fake_email(self, match: re.Match[str]) -> str:
        return f"user-{self._hex('email', match.group(0).lower())[:8]}@example.test"

    def _scrub_plain(self, text: str, digits: bool) -> str:
        for kind in _TOKEN_SCRUB_ORDER:
            pattern = dict(_SECRET_PATTERNS)[kind]
            text = pattern.sub(REDACTED_TOKEN, text)
        if digits:
            text = _DIGITS_RE.sub(lambda m: self._fake_digits(m.group(0)), text)
        text = _EMAIL_RE.sub(self._fake_email, text)
        text = self._apply_literals(text)
        if self._names:
            keys = sorted(self._names, key=len, reverse=True)
            pattern = re.compile(r"(?<![0-9A-Za-z])(?:" + "|".join(re.escape(k) for k in keys) + r")(?![0-9A-Za-z])")
            text = pattern.sub(lambda m: self._names[m.group(0)], text)
        return text

    def _scrub_text(self, text: str, digits: bool = True) -> str:
        out, last = [], 0
        for match in _URL_RE.finditer(text):
            url = match.group(0).rstrip(".,;)")
            out.append(self._scrub_plain(text[last:match.start()], digits))
            out.append(self.sanitize_url(url))
            last = match.start() + len(url)
        out.append(self._scrub_plain(text[last:], digits))
        return "".join(out)

    def _mask(self, text: str, key: str) -> str:
        words, index = [], int(self._hex("mask", key + text)[:8], 16)
        total = 0
        while total < len(text):
            word = _LOREM[index % len(_LOREM)]
            words.append(word)
            total += len(word) + 1
            index += 7
        return " ".join(words)[: len(text)]

    # --- structure ---------------------------------------------------------------------------

    def _id_value(self, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return int(self._fake_digits(str(value))) if len(str(value)) >= 5 else value
        if isinstance(value, str):
            if value.isdigit() and len(value) >= 5:
                return self._fake_digits(value)
            if len(value) >= 12 and re.fullmatch(r"[A-Za-z0-9_\-]+", value) and any(c.isdigit() for c in value):
                return self._fake_alnum(value)
            return self._scrub_text(value)
        if isinstance(value, (list, tuple)):
            return [self._id_value(v) for v in value]
        return self._walk(value)

    def _walk(self, node: Any, key: str | None = None, parent_key: str | None = None) -> Any:
        if isinstance(node, dict):
            person = self._is_person(node, parent_key)
            out = {}
            for k, v in node.items():
                nk = _norm_key(k)
                if nk in _SECRET_KEYS and isinstance(v, str) and v:
                    out[k] = REDACTED
                elif person and nk in _NAME_KEYS and isinstance(v, str) and v.strip():
                    out[k] = self._names.get(v, v)
                elif person and nk in ("first_name", "last_name") and isinstance(v, str) and len(v) >= 2:
                    out[k] = self._names.get(v, v)
                elif nk in _ID_KEYS or nk.endswith("_id") or nk.endswith("_ids"):
                    out[k] = self._id_value(v)
                elif any(part in nk for part in _PHONE_KEYS) and isinstance(v, str) and v:
                    out[k] = "+0000000000"
                elif nk in self._mask_keys and isinstance(v, str):
                    out[k] = self._mask(v, nk)
                else:
                    out[k] = self._walk(v, k, key)
            return out
        if isinstance(node, (list, tuple)):
            return [self._walk(item, key, parent_key) for item in node]
        if isinstance(node, str):
            skip_digits = key is not None and any(p in _norm_key(key) for p in _NO_DIGIT_SCRUB_KEY_PARTS)
            return self._scrub_text(node, digits=not skip_digits)
        return node

    def sanitize_value(self, value: Any) -> Any:
        """A scrubbed deep copy of a decoded JSON value; the input is never modified."""
        self._harvest(value)
        return self._walk(value)

    def sanitize_entry(self, entry: Mapping[str, Any]) -> dict:
        """Scrub one recorded exchange `{method, url, params?, status, json?|body_text?|body_base64?, content_type?}`."""
        self._harvest(entry.get("json"))
        out: dict[str, Any] = {}
        for key, value in entry.items():
            if key == "url":
                out[key] = self.sanitize_url(value)
            elif key == "params":
                out[key] = {k: self._scrub_text(v) if isinstance(v, str) else v for k, v in (value or {}).items()}
            elif key == "json":
                out[key] = self._walk(value)
            elif key == "body_text":
                out[key] = self._scrub_text(value)
            elif key == "body_base64":
                is_image = str(entry.get("content_type", "")).lower().startswith("image/")
                out[key] = STUB_PNG_BASE64 if is_image else STUB_BYTES_BASE64
            else:
                out[key] = value
        return out

    def sanitize_entries(self, entries: Iterable[Mapping[str, Any]]) -> list[dict]:
        entries = list(entries)
        for entry in entries:  # learn every name first so earlier text is scrubbed by later people
            self._harvest(entry.get("json"))
        return [self.sanitize_entry(e) for e in entries]
