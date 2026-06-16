"""Pure-logic tests for app.utils.upload_safety (no DB / no app needed)."""
import pytest
from fastapi import HTTPException

from app.utils.upload_safety import (
    validate_upload_filename,
    sanitize_display_filename,
    build_storage_filename,
)


@pytest.mark.parametrize("name", [
    "../evil.txt",
    "..\\evil.txt",
    "/tmp/evil.txt",
    "nested/evil.txt",
    "nested\\evil.txt",
    "evil.txt\x00.png",
    "../../etc/passwd.txt",
    "C:\\windows\\hosts.txt",
    ".txt",            # leading dot / extension-only
    ".bashrc.txt",     # leading dot
    "",                # empty
    "   ",             # blank
])
def test_validate_rejects_path_like_or_empty(name):
    with pytest.raises(HTTPException) as ei:
        validate_upload_filename(name)
    assert ei.value.status_code == 400


@pytest.mark.parametrize("name", [
    "novel.exe",
    "novel.pdf",
    "novel.txt.exe",
    "novel",
])
def test_validate_rejects_non_txt(name):
    with pytest.raises(HTTPException) as ei:
        validate_upload_filename(name)
    assert ei.value.status_code == 400


@pytest.mark.parametrize("name", [
    "novel.txt",
    "my story @ draft #1!.txt",
    "中文 文件名 @1.txt",
])
def test_validate_accepts_plain_txt(name):
    validate_upload_filename(name)  # must not raise


def test_sanitize_strips_dangerous_chars_keeps_txt():
    out = sanitize_display_filename("my story @ draft #1!.txt")
    assert out.endswith(".txt")
    for ch in ["/", "\\", " ", "@", "#", "!", ";", "&", "|", "`", "$"]:
        assert ch not in out


def test_sanitize_keeps_cjk():
    out = sanitize_display_filename("中文 文件名 @1.txt")
    assert out.endswith(".txt")
    assert "中文" in out
    assert " " not in out and "@" not in out


def test_sanitize_bounds_length():
    out = sanitize_display_filename("a" * 500 + ".txt")
    assert len(out) <= 100
    assert out.endswith(".txt")


def test_build_storage_filename_is_uuid_txt():
    a = build_storage_filename("my story @ draft #1!.txt")
    b = build_storage_filename("my story @ draft #1!.txt")
    assert a.endswith(".txt") and b.endswith(".txt")
    assert a != b                       # uuid → collision-free
    stem = a[:-4]
    assert len(stem) == 32 and all(c in "0123456789abcdef" for c in stem)
    # never carries the original name (no enumeration / path injection)
    assert "story" not in a and " " not in a and "/" not in a
