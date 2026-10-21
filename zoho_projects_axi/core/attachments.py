"""Images attached to a task or bug, fetched on request and under hard limits (T12, decision D12).

Both front-ends use this module; neither returns image bytes by default:

* CLI    `save_images(...)`    downloads into a private per-item directory and returns paths
* MCP    `inline_images(...)`  returns the bytes and content type, for the inline `Image` results (T13)

Both are `fetch_images(...)` underneath: take the attachment records the gateway already normalized
(`file_name`, `content_type`, `size`, `download_url`; the raw spellings `name`, `type` are read too), keep the
images, and download at most `max_images` of them through the gateway's `download` (https-only, Zoho hosts
only, the OAuth token goes nowhere else, and the byte cap is re-checked on the body). Whatever cannot be
delivered is returned as a `Skipped` entry with a reason, never raised: a record with no usable URL, an image
listed over the size cap, an image over the count cap, a refused host, a failed request. `ImageBatch.warnings()`
turns those into the lines a Result carries; the command still succeeds.

Files written by `save_images` are named after the attachment but never trusted: the name is cut to its last
path component, stripped of anything but word characters, dots, dashes, spaces and brackets, of leading dots
(no hidden files) and of trailing dots/spaces; an empty result becomes `image-N`; two attachments with the same
name (case-insensitively) get `-2`, `-3` ... in listing order, so a rerun maps every attachment to the same
file. Directories are created 0700 and files 0600; a file is written to a temporary name and renamed into
place, so an interrupted run leaves no half file that a rerun could mistake for a download.

Reuse (idempotence): an existing regular file whose size equals the size Zoho lists for the attachment is kept
and not downloaded again, unless `fresh`. A record that lists no size is always downloaded: there is nothing to
compare against. No I/O happens at import time.
"""
from __future__ import annotations

import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .errors import AuthError, RateLimitedError, ZohoAxiError

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic", ".tif", ".tiff")
MAX_NAME_CHARS = 100
_UNSAFE = re.compile(r"[^\w.\-+ ()\[\]@,]")
_NAMES_SHOWN = 5

Download = Callable[[str, "int | None"], "tuple[bytes, str]"]  # `ZohoGateway.download`


# --- classification and small helpers ----------------------------------------------------------------

def _text(record: Mapping[str, Any], *names: str) -> str:
    return next((str(record[n]).strip() for n in names if record.get(n) not in (None, "")), "")


def is_image(record: Mapping[str, Any]) -> bool:
    """An image by MIME type, else by file extension (Zoho sometimes says octet-stream for a .JPG)."""
    if _text(record, "content_type", "type").lower().startswith("image/"):
        return True
    return _text(record, "file_name", "name").lower().endswith(IMAGE_EXTENSIONS)


def human_bytes(value: Any) -> str:
    try:
        n = int(float(str(value)))
    except ValueError:
        return ""
    return f"{n} B" if n < 1024 else f"{n // 1024} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _size_of(record: Mapping[str, Any]) -> int | None:
    try:
        return int(float(_text(record, "size", "file_size")))
    except ValueError:
        return None


def default_attachments_dir(env: Mapping[str, str] | None = None) -> Path:
    """`$XDG_CACHE_HOME/zoho-projects-axi/attachments`, else `$HOME/.cache/zoho-projects-axi/attachments`. The environment is
    injected (never read implicitly) so tests and front-ends decide where files go."""
    env = os.environ if env is None else env
    base = env.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path(env.get("HOME") or Path.home()) / ".cache"
    return root / "zoho-projects-axi" / "attachments"


_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def item_dir(base: Path, kind: str, ident: str) -> Path:
    """The directory of one task or bug: `<base>/<kind>-<id>`, with anything odd in the id replaced."""
    return base / f"{_UNSAFE_ID.sub('_', kind)}-{_UNSAFE_ID.sub('_', str(ident))}"


def safe_filename(name: Any, fallback: str = "") -> str:
    """A file name that is safe to create inside a directory: never a path, never hidden, never empty
    when a fallback is given (see the module docstring for the rules)."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE.sub("_", base).lstrip(". ").rstrip(". ")
    if len(base) > MAX_NAME_CHARS:
        stem, dot, ext = base.rpartition(".")
        ext = ext[:16]
        base = (stem[: MAX_NAME_CHARS - len(ext) - 1] + "." + ext) if dot else base[:MAX_NAME_CHARS]
    return base or fallback


def _unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 1
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    while candidate.casefold() in taken:
        n += 1
        candidate = f"{stem}-{n}" + (f".{ext}" if dot else "")
    taken.add(candidate.casefold())
    return candidate


# --- results ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageFile:
    """One delivered image. `path` is set when it was saved to disk, `data` when it is held in memory."""

    name: str            # the attachment's own name, as Zoho lists it
    size: int            # bytes
    content_type: str
    path: Path | None = None
    data: bytes | None = None


@dataclass(frozen=True)
class Skipped:
    """An image that was not delivered. reason: no_url, oversized, over_count, failed."""

    name: str
    reason: str
    detail: str = ""


@dataclass
class ImageBatch:
    images: list[ImageFile] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    max_images: int = 0

    def warnings(self) -> list[str]:
        lines = [f"image {s.name}: {s.detail}" for s in self.skipped if s.reason != "over_count"]
        over = [s.name for s in self.skipped if s.reason == "over_count"]
        if over:
            shown = ", ".join(over[:_NAMES_SHOWN]) + (f" (+{len(over) - _NAMES_SHOWN} more)" if len(over) > _NAMES_SHOWN else "")
            lines.append(f"{len(over)} more image{'s' if len(over) != 1 else ''} not downloaded "
                         f"(max_images is {self.max_images}): {shown}")
        return lines


# --- the selection and download --------------------------------------------------------------------------

def fetch_images(
    download: Download,
    records: Sequence[Mapping[str, Any]],
    *,
    max_images: int,
    max_bytes: int,
    dest: Path | None = None,
    fresh: bool = False,
) -> ImageBatch:
    """The images among `records`, in listing order, at most `max_images` downloaded, each at most
    `max_bytes`. With `dest` they are written there (see `save_images`), without it they stay in memory.
    Rate limiting and a rejected token still raise: every later download would fail the same way."""
    batch = ImageBatch(max_images=max_images)
    taken: set[str] = set()
    attempted = 0
    for number, record in enumerate((r for r in records if is_image(r)), start=1):
        name = _text(record, "file_name", "name")
        label = name or f"image-{number}"
        filename = _unique(safe_filename(name, f"image-{number}"), taken)  # allocated even if skipped: stable names
        url = _text(record, "download_url")
        size = _size_of(record)
        if not url:
            batch.skipped.append(Skipped(label, "no_url", "no download URL in the attachment record"))
            continue
        if size is not None and size > max_bytes:
            batch.skipped.append(Skipped(label, "oversized", f"{human_bytes(size)} is over the "
                                         f"{human_bytes(max_bytes)} limit (max_attachment_bytes)"))
            continue
        if attempted >= max_images:
            batch.skipped.append(Skipped(label, "over_count"))
            continue
        attempted += 1
        content_type = _text(record, "content_type", "type")
        try:
            if dest is not None and not fresh and _reusable(dest / filename, size):
                batch.images.append(ImageFile(label, (dest / filename).stat().st_size, content_type, path=dest / filename))
                continue
            data, served_type = download(url, max_bytes)
            if len(data) > max_bytes:  # the gateway checks too; a second line costs nothing
                batch.skipped.append(Skipped(label, "failed", f"Attachment exceeds the {max_bytes}-byte cap."))
                continue
            kind = _content_type(served_type, content_type, label)
            if dest is None:
                batch.images.append(ImageFile(label, len(data), kind, data=data))
            else:
                batch.images.append(ImageFile(label, len(data), kind, path=_write_private(dest, filename, data)))
        except RateLimitedError:
            raise
        except AuthError as exc:
            if exc.code != "auth.forbidden":
                raise
            batch.skipped.append(Skipped(label, "failed", exc.message))
        except ZohoAxiError as exc:
            batch.skipped.append(Skipped(label, "failed", exc.message))
        except OSError as exc:
            batch.skipped.append(Skipped(label, "failed", f"could not save the file: {type(exc).__name__}"))
    return batch


def save_images(download: Download, records: Sequence[Mapping[str, Any]], *, dest: Path, max_images: int,
                max_bytes: int, fresh: bool = False) -> ImageBatch:
    """CLI: download the selected images into `dest` (created 0700) as 0600 files; `ImageFile.path` is set."""
    return fetch_images(download, records, max_images=max_images, max_bytes=max_bytes, dest=dest, fresh=fresh)


def inline_images(download: Download, records: Sequence[Mapping[str, Any]], *, max_images: int,
                  max_bytes: int) -> ImageBatch:
    """MCP: the same selection and caps, nothing written; `ImageFile.data` and `.content_type` feed an `Image`."""
    return fetch_images(download, records, max_images=max_images, max_bytes=max_bytes)


def _content_type(served: str, listed: str, name: str) -> str:
    """What Zoho served if that says image, else what the record listed, else a guess from the extension."""
    served = served.split(";")[0].strip().lower()
    listed = listed.split(";")[0].strip().lower()
    if served.startswith("image/"):
        return served
    if listed.startswith("image/"):
        return listed
    return mimetypes.guess_type(name)[0] or served or listed or "application/octet-stream"


# --- files ------------------------------------------------------------------------------------------------

def _private_dir(path: Path) -> None:
    """Create `path` and any missing parents as 0700 (umask-proof) and make `path` and its parent private;
    directories that already existed higher up are left alone."""
    missing = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        probe = probe.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    for directory in (path, path.parent):
        if directory not in missing and directory.is_dir() and not directory.is_symlink():
            os.chmod(directory, 0o700)


def _reusable(target: Path, listed_size: int | None) -> bool:
    if listed_size is None:
        return False
    try:
        info = target.lstat()
    except OSError:
        return False
    return (info.st_mode & 0o170000) == 0o100000 and info.st_size == listed_size  # a regular file, not a link


def _write_private(directory: Path, filename: str, data: bytes) -> Path:
    if filename in ("", ".", "..") or os.path.basename(filename) != filename or "\\" in filename:
        raise OSError("refusing to write outside the attachments directory")  # cannot happen after safe_filename
    target = directory / filename
    _private_dir(directory)  # lazily: a run that saves nothing creates nothing
    fd, temp = tempfile.mkstemp(prefix=".part-", dir=directory)  # created 0600
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, target)  # replaces a link at `target` itself, never what it points to
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return target
