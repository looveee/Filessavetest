"""Upload filename safety helpers (v0.6.2).

Policy (strategy A — reject path-like names outright):
  - A filename containing a path separator (``/`` or ``\\``), a NUL byte, a
    ``..`` path segment, or an absolute path is REJECTED with HTTP 400. We do
    NOT silently basename it — a path-like upload name is treated as hostile.
  - A name with ordinary "special" characters (spaces, punctuation, CJK) but
    no path separators is ALLOWED; we derive a safe display name from it.
  - Only ``.txt`` is accepted.
  - What actually lands on disk is a UUID-based name (see
    ``build_storage_filename``) so the user's original filename is never used
    to build a path, and stored files can't be enumerated or collided.

Three functions, three jobs:
  - ``validate_upload_filename`` — gatekeeper, raises 400 on anything unsafe.
  - ``sanitize_display_filename`` — derives a safe label for UI / metadata.
  - ``build_storage_filename``    — the actual on-disk name (uuid + .txt).
"""
from __future__ import annotations

import os
import re
import uuid

from fastapi import HTTPException

ALLOWED_EXTENSIONS = (".txt",)
_MAX_DISPLAY_LEN = 100
# Anything outside this set becomes "_" in the display name.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._\-一-鿿]")


def validate_upload_filename(filename: str) -> None:
    """Reject path-like or otherwise unsafe upload names. Raises HTTP 400.

    Allowed through: a non-empty name with a ``.txt`` extension and no path
    separators / NUL / ``..`` / absolute-path markers.
    """
    name = filename or ""
    if not name.strip():
        raise HTTPException(400, "filename is required")
    if "\x00" in name:
        raise HTTPException(400, "filename contains a NUL byte")
    if "/" in name or "\\" in name:
        raise HTTPException(400, "filename must not contain a path separator")
    # ``..`` as a whole path segment, or a leading drive/colon, is path-like.
    if ".." in name:
        raise HTTPException(400, "filename must not contain '..'")
    # A leading dot would make a hidden file; also blocks names that are only
    # an extension like ".txt".
    if name.startswith("."):
        raise HTTPException(400, "filename must not start with '.'")
    # Windows drive-absolute (e.g. C:\) — the ':' is the tell since separators
    # are already rejected above.
    if ":" in name:
        raise HTTPException(400, "filename must not contain ':'")

    _, ext = os.path.splitext(name.lower())
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"only {', '.join(ALLOWED_EXTENSIONS)} files are allowed")


def sanitize_display_filename(filename: str) -> str:
    """Return a safe, separator-free display name preserving the .txt suffix.

    Assumes ``validate_upload_filename`` already passed (so there are no path
    separators). Replaces every unsafe character with ``_`` and bounds the
    length. Never used to build an on-disk path.
    """
    # Defensive: drop any path component even though validation rejects them.
    base = os.path.basename(filename.replace("\\", "/")).strip()
    stem, ext = os.path.splitext(base)
    ext = ext.lower() if ext.lower() in ALLOWED_EXTENSIONS else ".txt"
    safe_stem = _UNSAFE_CHARS.sub("_", stem).strip("._") or "upload"
    safe = f"{safe_stem}{ext}"
    if len(safe) > _MAX_DISPLAY_LEN:
        safe = safe_stem[: _MAX_DISPLAY_LEN - len(ext)] + ext
    return safe


def build_storage_filename(original_filename: str = "") -> str:
    """Return a collision-free, non-enumerable on-disk name (uuid + .txt)."""
    _, ext = os.path.splitext((original_filename or "").lower())
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".txt"
    return f"{uuid.uuid4().hex}{ext}"
