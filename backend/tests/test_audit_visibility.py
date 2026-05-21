"""Audit log visibility — the row-level-isolation rules from v0.3.

Spec recap:
  - admin: sees all
  - project owner / member: sees only their own projects + own user-level
  - non-member explicit project_id filter -> 403
  - non-admin filtering by someone else's user_id -> 403
"""
import uuid
import pytest


def _register(client, prefix="user"):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"{prefix}_{suffix}",
        "email":    f"{prefix}_{suffix}@example.com",
        "password": "AuditTest123!",
    }
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_audit_visibility_outsider_isolated(client):
    # User A creates a project. User B is a complete outsider.
    a = _register(client, "audowner")
    b = _register(client, "audoutsider")

    pr = client.post("/api/projects", json={"name": f"audtest-{uuid.uuid4().hex[:6]}"},
                     headers=_auth(a["access_token"]))
    assert pr.status_code == 200, pr.text
    pid = pr.json()["id"]

    # Outsider explicit project_id filter must 403
    r = client.get(f"/api/audit-logs?project_id={pid}", headers=_auth(b["access_token"]))
    assert r.status_code == 403, r.text

    # Outsider unfiltered must NOT see any rows for project pid
    r = client.get("/api/audit-logs", headers=_auth(b["access_token"]))
    assert r.status_code == 200
    leak = [row for row in r.json() if row.get("project_id") == pid]
    assert leak == [], f"outsider saw rows for foreign project: {leak}"


def test_audit_visibility_owner_can_see_own_project(client):
    a = _register(client, "audowner2")
    pr = client.post("/api/projects", json={"name": f"audtest-{uuid.uuid4().hex[:6]}"},
                     headers=_auth(a["access_token"]))
    pid = pr.json()["id"]

    r = client.get(f"/api/audit-logs?project_id={pid}", headers=_auth(a["access_token"]))
    assert r.status_code == 200, r.text
    actions = {row["action"] for row in r.json()}
    assert "project.create" in actions, f"missing project.create row, got: {actions}"


def test_audit_visibility_user_id_filter_blocked_for_others(client):
    a = _register(client, "audselfA")
    b = _register(client, "audselfB")

    # B asks to filter by A's user_id → 403
    r = client.get(f"/api/audit-logs?user_id={a['user']['id']}", headers=_auth(b["access_token"]))
    assert r.status_code == 403


def test_audit_visibility_user_can_filter_own_user_id(client):
    a = _register(client, "audself2")
    r = client.get(f"/api/audit-logs?user_id={a['user']['id']}", headers=_auth(a["access_token"]))
    assert r.status_code == 200
    rows = r.json()
    # Should include the register row
    assert any(row["action"] == "user.register" for row in rows), rows


def test_audit_survives_project_delete(client):
    """Core invariant for v0.3 FK removal: deleting a project does NOT
    cascade-delete its audit rows."""
    # Need an admin to delete. The first user the test process registers
    # is admin in fresh DBs; if not, this test will skip.
    me = _register(client, "audadm")
    if not me["user"]["is_admin"]:
        pytest.skip("test DB already had users — first registrant is not admin")

    pr = client.post("/api/projects", json={"name": f"will-die-{uuid.uuid4().hex[:6]}"},
                     headers=_auth(me["access_token"]))
    pid = pr.json()["id"]

    dr = client.delete(f"/api/projects/{pid}", headers=_auth(me["access_token"]))
    assert dr.status_code == 200, dr.text

    # Now query audit by project_id — must still find both create + delete
    r = client.get(f"/api/audit-logs?project_id={pid}", headers=_auth(me["access_token"]))
    assert r.status_code == 200, r.text
    actions = {row["action"] for row in r.json()}
    assert "project.create" in actions
    assert "project.delete" in actions
