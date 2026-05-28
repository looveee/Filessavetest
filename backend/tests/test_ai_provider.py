"""v0.6 AI provider architecture tests.

Pure-logic tests run with no DB. DB/API tests skip automatically when
DATABASE_URL is unreachable (see conftest).
"""
import json
import uuid

import pytest

from app.services.ai import ai_service, get_provider, OP_SCHEMAS


def _u() -> str:
    return uuid.uuid4().hex[:8]


_CTX = {
    "theme": "复仇女王", "genre": "都市", "style": "快节奏",
    "episode_title": "第1集", "episode_number": 1, "episode_summary": "梗概",
    "script_text": "一段文案", "content": "正常内容", "total": 3,
    "shot_count": 3, "original_script": "原文案", "instruction": "整体重写",
}


# ---------------------------------------------------------------------
# 1. MockProvider returns schema-valid structured JSON for every op
# ---------------------------------------------------------------------
@pytest.mark.parametrize("op", sorted(OP_SCHEMAS.keys()))
def test_mock_provider_returns_structured_json(op):
    prov = get_provider()
    ctx = {**_CTX, "_op": op}
    resp = prov.generate_json("system", "user", context=ctx)
    obj = json.loads(resp.content)            # must be parseable JSON
    model = OP_SCHEMAS[op].model_validate(obj)  # must validate against schema
    assert model is not None
    assert resp.provider == "mock"
    assert resp.input_tokens > 0 and resp.output_tokens > 0


# ---------------------------------------------------------------------
# 2. A parse failure enters the repair path and recovers
# ---------------------------------------------------------------------
def test_json_parse_failure_is_repaired():
    prov = get_provider()
    resp = prov.generate_json(
        "s", "u",
        context={"_op": "generate_outline", "theme": "t", "_mock_mode": "bad_json_repairable"},
    )
    # strict json.loads must fail on the fenced/trailing-comma payload...
    with pytest.raises(Exception):
        json.loads(resp.content)
    # ...but the service repairs it.
    parsed, err = ai_service._parse_validate(resp.content, OP_SCHEMAS["generate_outline"])
    assert parsed is not None
    assert err is None


# ---------------------------------------------------------------------
# 3. Unrepairable output yields an error (task would be marked failed)
# ---------------------------------------------------------------------
def test_unrepairable_json_returns_error():
    prov = get_provider()
    resp = prov.generate_json(
        "s", "u",
        context={"_op": "generate_outline", "theme": "t", "_mock_mode": "bad_json_fatal"},
    )
    parsed, err = ai_service._parse_validate(resp.content, OP_SCHEMAS["generate_outline"])
    assert parsed is None
    assert err


# ---------------------------------------------------------------------
# 4. ai_generation_runs records template version and never stores an API key
# ---------------------------------------------------------------------
def test_run_records_template_version_no_api_key(db_session, monkeypatch):
    from app.config import settings
    from app.models import AIGenerationRun

    sentinel = "sk-SENTINEL-MUST-NOT-PERSIST"
    monkeypatch.setattr(settings, "AI_API_KEY", sentinel, raising=False)

    resp = ai_service.generate_outline(
        db_session, run_ctx={"project_id": None, "created_by": None}, theme="测试主题",
    )
    assert resp.ok

    run = db_session.query(AIGenerationRun).order_by(AIGenerationRun.id.desc()).first()
    assert run is not None
    assert run.prompt_template_key == "generate_outline"
    assert run.prompt_template_version == 1
    assert run.status == "completed"
    assert len(run.request_hash) == 64 and len(run.response_hash) == 64

    # No column may contain the API key in clear.
    for col in ("provider", "model", "prompt_template_key", "error_message",
                "request_hash", "response_hash"):
        assert sentinel not in str(getattr(run, col) or "")


# ---------------------------------------------------------------------
# 5. Repair failure marks the run failed (pipeline would fail the task)
# ---------------------------------------------------------------------
def test_repair_failure_records_failed_run(db_session, monkeypatch):
    import app.services.ai as A
    from app.models import AIGenerationRun
    from app.services.ai.schemas import AIResponse

    class BadProvider:
        name = "mock"
        model = "mock-1"

        def generate_json(self, system, user, **kw):
            return AIResponse(content="totally not json {{{", provider="mock", model="mock-1")

    monkeypatch.setattr(A, "get_provider", lambda *a, **k: BadProvider())

    resp = ai_service.generate_outline(
        db_session, run_ctx={"project_id": None, "created_by": None}, theme="x",
    )
    assert not resp.ok
    assert resp.error

    run = db_session.query(AIGenerationRun).order_by(AIGenerationRun.id.desc()).first()
    assert run.status == "failed"
    assert run.error_message


# ---------------------------------------------------------------------
# API tests — need a live app + DB
# ---------------------------------------------------------------------
def _register(client, prefix):
    name = f"{prefix}_{_u()}"
    r = client.post("/api/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": "Passw0rd!",
    })
    assert r.status_code == 200, r.text
    return r.json()


def _admin_and_user(client):
    import app.database as dbmod
    from app.models import User

    admin = _register(client, "adm")
    user = _register(client, "usr")
    # Force admin flag in DB regardless of registration order.
    s = dbmod.SessionLocal()
    try:
        u = s.query(User).filter(User.id == admin["user"]["id"]).first()
        u.is_admin = True
        u2 = s.query(User).filter(User.id == user["user"]["id"]).first()
        u2.is_admin = False
        s.commit()
    finally:
        s.close()
    return admin["access_token"], user["access_token"]


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_ai_providers_endpoint(client):
    admin_tok, user_tok = _admin_and_user(client)
    r = client.get("/api/ai/providers", headers=_auth(user_tok))
    assert r.status_code == 200
    body = r.json()
    assert "current_provider" in body and "providers" in body
    # Each provider entry exposes only safe metadata — no secret VALUES.
    allowed = {"name", "model", "configured", "missing_config"}
    for p in body["providers"]:
        assert set(p.keys()) <= allowed


def test_ai_test_admin_only(client):
    admin_tok, user_tok = _admin_and_user(client)
    r = client.post("/api/ai/test", json={"prompt": "hello"}, headers=_auth(user_tok))
    assert r.status_code == 403
    r = client.post("/api/ai/test", json={"prompt": "hello"}, headers=_auth(admin_tok))
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "mock"
    assert r.json()["ok"] is True


def test_prompt_templates_admin_only(client):
    admin_tok, user_tok = _admin_and_user(client)

    # non-admin cannot list
    r = client.get("/api/prompts", headers=_auth(user_tok))
    assert r.status_code == 403

    # admin can list (seeded by migration; service-default fallback otherwise)
    r = client.get("/api/prompts", headers=_auth(admin_tok))
    assert r.status_code == 200
    items = r.json()
    if not items:
        pytest.skip("prompt_templates not seeded in this DB")
    pid = items[0]["id"]

    # non-admin cannot edit
    r = client.put(f"/api/prompts/{pid}", json={"name": "hacked"}, headers=_auth(user_tok))
    assert r.status_code == 403

    # admin can edit + clone
    r = client.put(f"/api/prompts/{pid}", json={"name": "renamed"}, headers=_auth(admin_tok))
    assert r.status_code == 200 and r.json()["name"] == "renamed"

    r = client.post(f"/api/prompts/{pid}/clone", headers=_auth(admin_tok))
    assert r.status_code == 200
    assert r.json()["version"] >= 2
    assert r.json()["is_active"] is False
