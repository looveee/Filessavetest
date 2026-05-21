"""Rate limit tests.

We use the test-only Redis DB defined in conftest (REDIS_URL ends in /15)
and tighten the lookup limit to 3/window so we can exhaust it quickly
without burning real seconds.
"""
import os
import uuid
import pytest


def _register(client, prefix="rl"):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"{prefix}_{suffix}",
        "email":    f"{prefix}_{suffix}@example.com",
        "password": "RatePass123!",
    }
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_lookup_rate_limit_returns_429(client, flush_redis):
    """conftest sets RL_LOOKUP_IP_LIMIT=3. The 4th hit must be 429."""
    me = _register(client, "rllookup")
    tok = me["access_token"]

    # First N (limit) requests succeed (404-or-empty body, but 200)
    limit = int(os.environ.get("RL_LOOKUP_IP_LIMIT", "3"))
    for i in range(limit):
        r = client.get(f"/api/users/lookup?username=nobody_{i}",
                       headers=_auth(tok))
        assert r.status_code == 200, f"hit {i+1} should pass: {r.status_code} {r.text}"

    # The (limit+1)-th must be 429
    r = client.get("/api/users/lookup?username=stillnobody",
                   headers=_auth(tok))
    assert r.status_code == 429, f"expected 429, got {r.status_code} {r.text}"
    assert "Retry-After" in r.headers


def test_lookup_429_writes_audit(client, flush_redis):
    """Hitting the limit must write a `user.lookup_rate_limited` audit row."""
    me = _register(client, "rlauditlog")
    tok = me["access_token"]
    me_id = me["user"]["id"]

    limit = int(os.environ.get("RL_LOOKUP_IP_LIMIT", "3"))
    for i in range(limit):
        client.get(f"/api/users/lookup?username=zz_{i}", headers=_auth(tok))
    # Trip the limit
    r = client.get("/api/users/lookup?username=zz_trip", headers=_auth(tok))
    assert r.status_code == 429

    # Check audit
    r = client.get(f"/api/audit-logs?user_id={me_id}", headers=_auth(tok))
    assert r.status_code == 200
    actions = [row["action"] for row in r.json()]
    assert "user.lookup_rate_limited" in actions, \
        f"expected user.lookup_rate_limited, saw: {actions}"


def test_login_invalid_creds_returns_generic_error(client, flush_redis):
    """v0.3: login error must NOT disclose whether the account exists."""
    # Hit a known-nonexistent user
    r1 = client.post("/api/auth/login",
                     json={"username": f"ghost_{uuid.uuid4().hex[:6]}", "password": "wrong"})
    # Register a real user, attempt with wrong password
    me = _register(client, "rlreal")
    r2 = client.post("/api/auth/login",
                     json={"username": me["user"]["username"], "password": "definitely_wrong"})

    assert r1.status_code == 401
    assert r2.status_code == 401
    # Detail string must be identical — no "user not found" vs "wrong password" leak
    assert r1.json().get("detail") == r2.json().get("detail")


def test_login_rate_limit_per_ip(client, flush_redis):
    """Limit per-IP should kick in before per-username (default IP=10/min,
    per-user=10/5min). We hit a fixed nonexistent username 11+ times and
    expect 429 within the first ~12 attempts."""
    saw_429 = False
    for i in range(15):
        r = client.post("/api/auth/login",
                        json={"username": f"nobody_{uuid.uuid4().hex[:4]}",
                              "password": "x"})
        if r.status_code == 429:
            saw_429 = True
            assert "Retry-After" in r.headers
            break
    assert saw_429, "rate limiter never triggered after 15 login attempts"


def test_register_rate_limit(client, flush_redis):
    """RL_REGISTER_IP_LIMIT default is 10/hour. We expect 429 by attempt ~12.
    Each registration uses a fresh username so we never hit the 'already
    taken' path."""
    saw_429 = False
    for i in range(15):
        suffix = uuid.uuid4().hex[:10]
        r = client.post("/api/auth/register", json={
            "username": f"rl_burst_{suffix}",
            "email":    f"rl_burst_{suffix}@example.com",
            "password": "RatePass123!",
        })
        if r.status_code == 429:
            saw_429 = True
            assert "Retry-After" in r.headers
            break
    assert saw_429, "register limiter never triggered"
