"""Audit log visibility — the row-level-isolation rules from v0.3.

Spec recap:
  - admin: sees all
  - project owner / member: sees only their own projects + own user-level
  - non-member explicit project_id filter -> 403
  - non-admin filtering by someone else's user_id -> 403

Users are created via the DB factory (conftest.create_test_user / user_factory)
rather than the register endpoint, so these tests never touch the register
rate limiter. See "测试环境与限流说明" in the README.
"""
import uuid


def test_audit_visibility_outsider_isolated(client, user_factory, auth_headers):
    # User A creates a project. User B is a complete outsider.
    a = user_factory()
    b = user_factory()

    pr = client.post("/api/projects", json={"name": f"audtest-{uuid.uuid4().hex[:6]}"},
                     headers=auth_headers(a))
    assert pr.status_code == 200, pr.text
    pid = pr.json()["id"]

    # Outsider explicit project_id filter must 403
    r = client.get(f"/api/audit-logs?project_id={pid}", headers=auth_headers(b))
    assert r.status_code == 403, r.text

    # Outsider unfiltered must NOT see any rows for project pid
    r = client.get("/api/audit-logs", headers=auth_headers(b))
    assert r.status_code == 200
    leak = [row for row in r.json() if row.get("project_id") == pid]
    assert leak == [], f"outsider saw rows for foreign project: {leak}"


def test_audit_visibility_owner_can_see_own_project(client, user_factory, auth_headers):
    a = user_factory()
    pr = client.post("/api/projects", json={"name": f"audtest-{uuid.uuid4().hex[:6]}"},
                     headers=auth_headers(a))
    pid = pr.json()["id"]

    r = client.get(f"/api/audit-logs?project_id={pid}", headers=auth_headers(a))
    assert r.status_code == 200, r.text
    actions = {row["action"] for row in r.json()}
    assert "project.create" in actions, f"missing project.create row, got: {actions}"


def test_audit_visibility_user_id_filter_blocked_for_others(client, user_factory, auth_headers):
    a = user_factory()
    b = user_factory()

    # B asks to filter by A's user_id → 403
    r = client.get(f"/api/audit-logs?user_id={a.id}", headers=auth_headers(b))
    assert r.status_code == 403


def test_audit_visibility_user_can_filter_own_user_id(client, user_factory, auth_headers):
    a = user_factory()
    # Generate an audit row attributable to this user by creating a project.
    pr = client.post("/api/projects", json={"name": f"audtest-{uuid.uuid4().hex[:6]}"},
                     headers=auth_headers(a))
    assert pr.status_code == 200, pr.text

    r = client.get(f"/api/audit-logs?user_id={a.id}", headers=auth_headers(a))
    assert r.status_code == 200
    rows = r.json()
    assert any(row["action"] == "project.create" for row in rows), rows


def test_audit_survives_project_delete(client, user_factory, auth_headers):
    """Core invariant for v0.3 FK removal: deleting a project does NOT
    cascade-delete its audit rows."""
    me = user_factory(is_admin=True)

    pr = client.post("/api/projects", json={"name": f"will-die-{uuid.uuid4().hex[:6]}"},
                     headers=auth_headers(me))
    pid = pr.json()["id"]

    dr = client.delete(f"/api/projects/{pid}", headers=auth_headers(me))
    assert dr.status_code == 200, dr.text

    # Now query audit by project_id — must still find both create + delete
    r = client.get(f"/api/audit-logs?project_id={pid}", headers=auth_headers(me))
    assert r.status_code == 200, r.text
    actions = {row["action"] for row in r.json()}
    assert "project.create" in actions
    assert "project.delete" in actions
