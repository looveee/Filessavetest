"""Upload security tests.

Exercises the path-traversal / extension-whitelist defences in
`POST /projects/{id}/source` through the real FastAPI app.

Filename policy (v0.6.2, strategy A):
  - Any path-like name (contains / \\ NUL .. or an absolute/drive marker) is
    REJECTED with 400 — never silently basenamed.
  - Ordinary special characters (spaces, punctuation, CJK) are ALLOWED and
    sanitized into a separator-free display name.
  - Only .txt. The on-disk file is a UUID name, never the user's filename.

Users come from the DB factory (conftest), so these tests don't hit the
register rate limiter.
"""
import io
import uuid

import pytest


def _new_project(client, headers):
    r = client.post(
        "/api/projects",
        json={"name": f"upload-test-{uuid.uuid4().hex[:6]}"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload(client, headers, pid, filename, content):
    return client.post(
        f"/api/projects/{pid}/source",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        headers=headers,
    )


def test_normal_txt_accepted(client, user_factory, auth_headers):
    h = auth_headers(user_factory())
    pid = _new_project(client, h)
    r = _upload(client, h, pid, "novel.txt", "hello world\n第一章".encode("utf-8"))
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "novel.txt"


@pytest.mark.parametrize("name", [
    "novel.exe",
    "novel.txt.exe",
    "novel.pdf",
    "novel",                     # no extension
])
def test_non_txt_rejected(client, user_factory, auth_headers, name):
    h = auth_headers(user_factory())
    pid = _new_project(client, h)
    r = _upload(client, h, pid, name, b"contents")
    assert r.status_code == 400, f"{name} should be rejected, got {r.status_code} {r.text}"


# NOTE: Windows drive paths ("C:\\...") and NUL-in-filename are exercised at
# the function level in test_upload_safety.py — the ASGI multipart transport
# mangles those before they reach the endpoint, so they're not reliable to
# assert over HTTP. Here we cover the path-like shapes that arrive verbatim.
@pytest.mark.parametrize("name", [
    "../evil.txt",
    "..\\evil.txt",
    "/tmp/evil.txt",
    "nested/evil.txt",
    "nested\\evil.txt",
    "../../etc/passwd.txt",
])
def test_path_like_filename_rejected(client, user_factory, auth_headers, name):
    """Strategy A: any path-like filename is rejected outright with 400 —
    we do NOT basename it into an accepted upload."""
    h = auth_headers(user_factory())
    pid = _new_project(client, h)
    r = _upload(client, h, pid, name, b"clean content")
    assert r.status_code == 400, f"{name!r} must be rejected, got {r.status_code} {r.text}"


def test_dotfile_rejected(client, user_factory, auth_headers):
    """A leading-dot name (`.txt`, `.bashrc.txt`) is rejected — never a hidden
    file, never an extension-only name."""
    h = auth_headers(user_factory())
    pid = _new_project(client, h)
    r = _upload(client, h, pid, ".txt", b"x")
    assert r.status_code == 400


def test_null_byte_in_content_rejected(client, user_factory, auth_headers):
    h = auth_headers(user_factory())
    pid = _new_project(client, h)
    r = _upload(client, h, pid, "novel.txt", b"hello\x00world")
    assert r.status_code == 400


def test_empty_filename_rejected(client, user_factory, auth_headers):
    h = auth_headers(user_factory())
    pid = _new_project(client, h)
    r = _upload(client, h, pid, "", b"x")
    # Either our 400 (empty name) or FastAPI's 422 (no valid file part) — both
    # are rejections; the multipart layer decides which fires first.
    assert r.status_code in (400, 422), r.text


def test_oversize_rejected(client, user_factory, auth_headers):
    """50 MB hard limit (config default). We send 51 MB."""
    h = auth_headers(user_factory())
    pid = _new_project(client, h)
    big = b"a" * (51 * 1024 * 1024)
    r = _upload(client, h, pid, "huge.txt", big)
    assert r.status_code == 413


@pytest.mark.parametrize("name", [
    "my story @ draft #1!.txt",
    "中文 文件名 @1.txt",
])
def test_special_chars_sanitized(client, user_factory, auth_headers, name):
    """Ordinary special characters (no path separators) are accepted; the
    stored display name is sanitized and separator-free."""
    h = auth_headers(user_factory())
    pid = _new_project(client, h)
    r = _upload(client, h, pid, name, b"safe")
    assert r.status_code == 200, r.text
    fn = r.json()["filename"]
    assert fn.endswith(".txt")
    # No path separators or dangerous shell metacharacters survive.
    for ch in ["/", "\\", "\x00", ";", "&", "|", "`", "$", "(", ")", " ", "@", "#", "!"]:
        assert ch not in fn, f"char {ch!r} survived sanitization in {fn!r}"
