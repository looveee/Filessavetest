"""v0.6 AI provider architecture tests.

Pure-logic tests run with no DB. DB/API tests skip automatically when
DATABASE_URL is unreachable (see conftest).
"""
import json
import uuid

import pytest

from app.services.ai import ai_service, get_provider, OP_SCHEMAS
from app.services.ai.json_repair import repair_loads


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
# 6. json_repair handles the messy shapes real models emit
# ---------------------------------------------------------------------
def test_repair_strips_markdown_code_fence():
    raw = '```json\n{"a": 1, "b": "x"}\n```'
    assert repair_loads(raw) == {"a": 1, "b": "x"}


def test_repair_extracts_object_from_surrounding_prose():
    raw = 'Sure, here is the JSON you asked for:\n{"a": 1, "b": [2, 3]}\nHope this helps!'
    assert repair_loads(raw) == {"a": 1, "b": [2, 3]}


def test_repair_tolerates_trailing_comma():
    assert repair_loads('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_repair_raises_on_non_json_text():
    with pytest.raises(ValueError):
        repair_loads("I'm sorry, I can't help with that.")


# ---------------------------------------------------------------------
# 7. HTTP retry policy: 5xx retries, 4xx does NOT, timeout retries then fails
# ---------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status_code, text="upstream said no"):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {"ok": True}


def _fake_client_factory(behavior, calls):
    """behavior: callable(call_index) -> _FakeResp, or raises."""
    import httpx

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            i = calls["n"]
            calls["n"] += 1
            return behavior(i)

    return _FakeClient, httpx


def test_http_post_retries_on_5xx_then_raises(monkeypatch):
    from app.services.ai import base as B

    calls = {"n": 0}
    FakeClient, httpx = _fake_client_factory(lambda i: _FakeResp(503), calls)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    p = B.BaseProvider(max_retries=2)
    with pytest.raises(B.ProviderServerError):
        p._http_post("http://x", {}, {})
    assert calls["n"] == 3  # initial + 2 retries


def test_http_post_does_not_retry_on_4xx(monkeypatch):
    from app.services.ai import base as B

    calls = {"n": 0}
    FakeClient, httpx = _fake_client_factory(lambda i: _FakeResp(401), calls)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    p = B.BaseProvider(max_retries=3)
    with pytest.raises(B.ProviderClientError):
        p._http_post("http://x", {}, {})
    assert calls["n"] == 1  # 4xx fails fast, no retry


def test_http_post_timeout_retries_then_raises(monkeypatch):
    from app.services.ai import base as B
    import httpx

    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            calls["n"] += 1
            raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    p = B.BaseProvider(max_retries=1, timeout=5)
    with pytest.raises(B.ProviderTimeoutError):
        p._http_post("http://x", {}, {})
    assert calls["n"] == 2  # initial + 1 retry


def test_http_post_succeeds_first_try(monkeypatch):
    from app.services.ai import base as B

    calls = {"n": 0}
    FakeClient, httpx = _fake_client_factory(lambda i: _FakeResp(200), calls)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    p = B.BaseProvider(max_retries=2)
    body, latency = p._http_post("http://x", {}, {})
    assert body == {"ok": True}
    assert calls["n"] == 1


# ---------------------------------------------------------------------
# 8. Per-user daily AI-call soft cap
# ---------------------------------------------------------------------
def test_daily_limit_disabled_or_no_user():
    from app.services.ratelimit import ai_daily_call_check

    allowed, count = ai_daily_call_check(1, limit=0)  # disabled
    assert allowed and count == 0
    allowed, _ = ai_daily_call_check(None)  # anonymous
    assert allowed


def test_daily_limit_blocks_when_over(monkeypatch):
    import app.services.ratelimit as RL

    monkeypatch.setattr(RL, "hit", lambda *a, **k: (False, 201, 60))
    allowed, count = RL.ai_daily_call_check(7, limit=200)
    assert not allowed and count == 201


def test_daily_limit_marks_task_failed(db_session, monkeypatch):
    """When over the daily cap, the dispatcher fails the task and never calls
    the provider."""
    import app.tasks.ai_tasks as T
    from app.models import User, Project, GenerationTask, TaskType, TaskStatus

    uname = f"lim_{_u()}"
    u = User(email=f"{uname}@example.com", username=uname, hashed_password="x", is_admin=False)
    db_session.add(u)
    db_session.commit()
    p = Project(name=f"lim {uname}", owner_id=u.id)
    db_session.add(p)
    db_session.commit()
    task = GenerationTask(
        project_id=p.id, task_type=TaskType.outline_generation,
        status=TaskStatus.pending, owner_id=u.id, created_by=u.id, input_data={},
    )
    db_session.add(task)
    db_session.commit()

    monkeypatch.setattr(T, "ai_daily_call_check", lambda *a, **k: (False, 999))
    rejected = T._daily_limit_exceeded(db_session, task)
    assert rejected is True
    db_session.refresh(task)
    assert task.status == TaskStatus.failed
    assert "limit" in (task.error or "").lower()


# ---------------------------------------------------------------------
# API tests — need a live app + DB. Users come from the DB factory (conftest)
# so these never hit the register rate limiter.
# ---------------------------------------------------------------------
def test_ai_providers_endpoint(client, user_factory, auth_headers):
    user_h = auth_headers(user_factory(is_admin=False))
    r = client.get("/api/ai/providers", headers=user_h)
    assert r.status_code == 200
    body = r.json()
    assert "current_provider" in body and "providers" in body
    # Each provider entry exposes only safe metadata — no secret VALUES.
    allowed = {"name", "model", "configured", "missing_config"}
    for p in body["providers"]:
        assert set(p.keys()) <= allowed


def test_ai_test_admin_only(client, user_factory, auth_headers):
    admin_h = auth_headers(user_factory(is_admin=True))
    user_h = auth_headers(user_factory(is_admin=False))
    r = client.post("/api/ai/test", json={"prompt": "hello"}, headers=user_h)
    assert r.status_code == 403
    r = client.post("/api/ai/test", json={"prompt": "hello"}, headers=admin_h)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "mock"
    assert r.json()["ok"] is True


def test_prompt_templates_admin_only(client, user_factory, auth_headers):
    admin_h = auth_headers(user_factory(is_admin=True))
    user_h = auth_headers(user_factory(is_admin=False))

    # non-admin cannot list
    r = client.get("/api/prompts", headers=user_h)
    assert r.status_code == 403

    # admin can list (seeded by migration; service-default fallback otherwise)
    r = client.get("/api/prompts", headers=admin_h)
    assert r.status_code == 200
    items = r.json()
    if not items:
        pytest.skip("prompt_templates not seeded in this DB")
    pid = items[0]["id"]

    # non-admin cannot edit
    r = client.put(f"/api/prompts/{pid}", json={"name": "hacked"}, headers=user_h)
    assert r.status_code == 403

    # admin can edit + clone
    r = client.put(f"/api/prompts/{pid}", json={"name": "renamed"}, headers=admin_h)
    assert r.status_code == 200 and r.json()["name"] == "renamed"

    r = client.post(f"/api/prompts/{pid}/clone", headers=admin_h)
    assert r.status_code == 200
    assert r.json()["version"] >= 2
    assert r.json()["is_active"] is False
