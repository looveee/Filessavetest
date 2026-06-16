"""Row-level access control for GET /api/ai/runs (v0.6.1).

  - admin sees everything
  - project owner sees their project's runs
  - project member sees runs for projects they belong to
  - a normal user cannot enumerate another project's runs via ?project_id
  - the payload exposes request_hash / response_hash but NEVER a prompt,
    raw_response, or API key

DB/API tests skip automatically when DATABASE_URL is unreachable (conftest).
"""
import uuid

import pytest


def _u() -> str:
    return uuid.uuid4().hex[:8]


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def _make_user(s, prefix, is_admin=False):
    """Create a user straight in the DB (no /register call, so these tests
    never touch the register rate limiter) and mint a token for them."""
    from app.models import User
    from app.security import create_access_token

    name = f"{prefix}_{_u()}"
    u = User(email=f"{name}@example.com", username=name,
             hashed_password="x", is_admin=is_admin, is_active=True)
    s.add(u)
    s.commit()
    s.refresh(u)
    return u.id, create_access_token(u.id)


def _setup(client):
    """Build admin / owner / member / outsider plus a project with two AI runs
    (one for the project, one unrelated). Returns tokens + identifiers."""
    import app.database as dbmod
    from app.models import Project, ProjectMember, ProjectRole, AIGenerationRun

    s = dbmod.SessionLocal()
    try:
        _, admin_tok = _make_user(s, "adm", is_admin=True)
        owner_id, owner_tok = _make_user(s, "own")
        member_id, member_tok = _make_user(s, "mem")
        _, outsider_tok = _make_user(s, "out")

        proj = Project(name=f"runs-sec-{_u()}", owner_id=owner_id)
        s.add(proj)
        s.commit()
        s.refresh(proj)

        s.add(ProjectMember(project_id=proj.id, user_id=member_id, role=ProjectRole.editor))

        tag = _u()
        s.add(AIGenerationRun(
            project_id=proj.id, provider="mock", model=f"in-{tag}",
            status="completed", request_hash="a" * 64, response_hash="b" * 64,
        ))
        # An unrelated project's run the owner/member must NOT see.
        s.add(AIGenerationRun(
            project_id=proj.id + 10_000_000, provider="mock", model=f"out-{tag}",
            status="completed", request_hash="c" * 64, response_hash="d" * 64,
        ))
        s.commit()
        proj_id = proj.id
    finally:
        s.close()

    return {
        "admin": admin_tok, "owner": owner_tok, "member": member_tok,
        "outsider": outsider_tok, "proj_id": proj_id, "tag": tag,
    }


def test_admin_sees_all_runs(client):
    ctx = _setup(client)
    r = client.get("/api/ai/runs", headers=_auth(ctx["admin"]))
    assert r.status_code == 200
    models = {row["model"] for row in r.json()}
    assert f"in-{ctx['tag']}" in models
    assert f"out-{ctx['tag']}" in models  # admin sees the unrelated one too


def test_owner_sees_only_their_project(client):
    ctx = _setup(client)
    r = client.get(f"/api/ai/runs?project_id={ctx['proj_id']}", headers=_auth(ctx["owner"]))
    assert r.status_code == 200
    rows = r.json()
    assert rows and all(row["project_id"] == ctx["proj_id"] for row in rows)
    assert any(row["model"] == f"in-{ctx['tag']}" for row in rows)


def test_member_sees_authorized_project(client):
    ctx = _setup(client)
    r = client.get(f"/api/ai/runs?project_id={ctx['proj_id']}", headers=_auth(ctx["member"]))
    assert r.status_code == 200
    assert any(row["model"] == f"in-{ctx['tag']}" for row in r.json())


def test_outsider_cannot_enumerate_other_project(client):
    ctx = _setup(client)
    r = client.get(f"/api/ai/runs?project_id={ctx['proj_id']}", headers=_auth(ctx["outsider"]))
    assert r.status_code == 403


def test_outsider_listing_excludes_other_project_runs(client):
    """Without a project_id filter the outsider simply gets nothing of ours."""
    ctx = _setup(client)
    r = client.get("/api/ai/runs", headers=_auth(ctx["outsider"]))
    assert r.status_code == 200
    models = {row["model"] for row in r.json()}
    assert f"in-{ctx['tag']}" not in models
    assert f"out-{ctx['tag']}" not in models


def test_run_payload_has_hashes_but_no_prompt_or_secret(client):
    ctx = _setup(client)
    r = client.get(f"/api/ai/runs?project_id={ctx['proj_id']}", headers=_auth(ctx["owner"]))
    assert r.status_code == 200
    row = r.json()[0]
    # Hashes are exposed for audit/dedupe.
    assert len(row["request_hash"]) == 64
    assert len(row["response_hash"]) == 64
    # Prompt text and raw provider response must never be serialized.
    for forbidden in ("prompt", "raw_response", "system_prompt", "user_prompt", "api_key"):
        assert forbidden not in row
