"""Upload security tests.

Exercises the path-traversal / extension-whitelist defences in
`POST /projects/{id}/source` through the real FastAPI app.
"""
import io
import time
import uuid

import pytest


def _register_admin(client):
    """Register a user. The first registered user becomes admin; if the DB
    has prior data this user will not be admin and we fall back to creating
    a project under whatever permissions we have. For upload tests we only
    need someone who can create a project + upload."""
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"upload_{suffix}",
        "email":    f"upload_{suffix}@example.com",
        "password": "UploadTest123!",
    }
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _new_project(client, token):
    r = client.post(
        "/api/projects",
        json={"name": f"upload-test-{uuid.uuid4().hex[:6]}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload(client, token, pid, filename, content):
    return client.post(
        f"/api/projects/{pid}/source",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_normal_txt_accepted(client):
    tok = _register_admin(client)
    pid = _new_project(client, tok)
    r = _upload(client, tok, pid, "novel.txt", "hello world\n第一章".encode("utf-8"))
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "novel.txt"


@pytest.mark.parametrize("name", [
    "novel.exe",
    "novel.txt.exe",
    "novel.pdf",
    "../../etc/passwd",          # has no .txt
    "novel",                     # no extension
])
def test_non_txt_rejected(client, name):
    tok = _register_admin(client)
    pid = _new_project(client, tok)
    r = _upload(client, tok, pid, name, b"contents")
    assert r.status_code == 400, f"{name} should be rejected, got {r.status_code} {r.text}"


@pytest.mark.parametrize("name,want_basename", [
    ("../../etc/passwd.txt",          "passwd.txt"),
    ("..\\..\\windows\\hosts.txt",    "hosts.txt"),
    ("/etc/shadow.txt",               "shadow.txt"),
    ("a/b/c/inner.txt",               "inner.txt"),
])
def test_path_traversal_neutralized(client, name, want_basename):
    """The persisted filename must be the basename only — never a path."""
    tok = _register_admin(client)
    pid = _new_project(client, tok)
    r = _upload(client, tok, pid, name, b"clean content")
    assert r.status_code == 200, r.text
    fn = r.json()["filename"]
    # Basename comparison after the sanitizer's regex (which may have replaced chars)
    assert "/" not in fn and "\\" not in fn
    assert ".." not in fn
    assert fn.endswith(".txt")
    # The dangerous-looking path component is gone
    assert want_basename in fn or want_basename.replace(".txt", "") in fn


def test_dotfile_rejected(client):
    """A name like `.txt` or `.bashrc.txt` after lstrip('.') becomes either
    empty or strips the leading dots; never a hidden file."""
    tok = _register_admin(client)
    pid = _new_project(client, tok)
    # `.txt` -> after lstrip('.') -> `txt` -> no extension -> 400
    r = _upload(client, tok, pid, ".txt", b"x")
    assert r.status_code == 400


def test_null_byte_in_content_rejected(client):
    tok = _register_admin(client)
    pid = _new_project(client, tok)
    r = _upload(client, tok, pid, "novel.txt", b"hello\x00world")
    assert r.status_code == 400


def test_oversize_rejected(client):
    """50 MB hard limit (config default). We send 51 MB."""
    tok = _register_admin(client)
    pid = _new_project(client, tok)
    big = b"a" * (51 * 1024 * 1024)
    r = _upload(client, tok, pid, "huge.txt", big)
    assert r.status_code == 413


def test_filename_special_chars_replaced(client):
    """Shell-meta characters in filename get replaced with underscores so a
    downstream tool that trusts the filename can't be tricked."""
    tok = _register_admin(client)
    pid = _new_project(client, tok)
    r = _upload(client, tok, pid, "a; rm -rf /.txt", b"safe")
    assert r.status_code == 200, r.text
    fn = r.json()["filename"]
    # No shell metachars in stored name
    for ch in [";", "&", "|", "`", "$", "(", ")", " "]:
        assert ch not in fn, f"special char {ch!r} survived sanitization in {fn!r}"
