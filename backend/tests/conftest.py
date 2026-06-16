"""Shared pytest fixtures.

Tests run against a real PostgreSQL + Redis pointed to by environment
variables. We do NOT spin up containers from inside pytest — assume the
caller has them up (e.g. `docker-compose up -d db redis`) or has set
DATABASE_URL / REDIS_URL to reachable services.

Each test that needs DB writes gets a fresh schema by running the
metadata create_all into a per-test schema name, then dropping it. We
keep this simple: one shared session per test, rolled back at teardown
where possible. For tests that hit the FastAPI TestClient, we use the
running app and clean the touched rows manually if needed.

If DATABASE_URL is unset or unreachable the relevant tests `pytest.skip`
rather than fail — local devs without a Postgres handy can still run
the pure-logic tests (permission matrix, upload sanitizer).
"""

import os
import sys
import pytest

# Make `app` importable when pytest runs from backend/ root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Default fallbacks — override via env in CI
os.environ.setdefault("DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/shortvideo_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/15")
os.environ.setdefault("SECRET_KEY", "test-secret-do-not-use-in-prod")
os.environ.setdefault("RATE_LIMIT_ENABLED", "true")
# Tighten lookup limit drastically for the rate-limit test
os.environ.setdefault("RL_LOOKUP_IP_LIMIT", "3")
os.environ.setdefault("RL_LOOKUP_IP_WINDOW", "60")


def _have_db():
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _have_redis():
    try:
        import redis as r
        cli = r.from_url(os.environ["REDIS_URL"], socket_timeout=1)
        cli.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def has_db():
    return _have_db()


@pytest.fixture(scope="session")
def has_redis():
    return _have_redis()


@pytest.fixture(scope="session")
def db_engine(has_db):
    if not has_db:
        pytest.skip("DATABASE_URL not reachable")
    from sqlalchemy import create_engine
    eng = create_engine(os.environ["DATABASE_URL"])
    # Make sure schema exists
    from app.database import Base
    Base.metadata.create_all(bind=eng)
    yield eng


@pytest.fixture()
def db_session(db_engine):
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def client(has_db):
    """FastAPI TestClient. Skips when DB isn't reachable."""
    if not has_db:
        pytest.skip("DATABASE_URL not reachable")
    from fastapi.testclient import TestClient
    # Import lazily so tests that don't need it still work
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def flush_redis(has_redis):
    """Flush the test-only Redis DB at the start of a test."""
    if not has_redis:
        pytest.skip("REDIS_URL not reachable")
    import redis as r
    cli = r.from_url(os.environ["REDIS_URL"])
    cli.flushdb()
    yield cli
    cli.flushdb()


# ---------------------------------------------------------------------
# Test user factory + auth helpers (v0.6.2)
#
# Non-auth/non-rate-limit tests MUST create users through this factory
# instead of POSTing /api/auth/register. The TestClient sends every request
# from a single client IP, so dozens of register calls would pile into the
# same per-IP bucket and trip the (production-default) limiter, making
# unrelated tests flaky. Creating users straight in the DB sidesteps that
# without weakening any production default.
# ---------------------------------------------------------------------
DEFAULT_TEST_PASSWORD = "Passw0rd!"


def create_test_user(db, username=None, email=None,
                     password=DEFAULT_TEST_PASSWORD, is_admin=False):
    """Insert a user directly into the DB with a properly hashed password.

    username/email auto-generate (unique) when omitted. Returns the User.
    """
    import uuid as _uuid
    from app.models import User
    from app.security import hash_password

    if username is None:
        username = "u_" + _uuid.uuid4().hex[:10]
    if email is None:
        email = f"{username}@example.com"
    u = User(
        username=username,
        email=email,
        full_name=username,
        hashed_password=hash_password(password),
        is_admin=is_admin,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def user_factory(db_session):
    """Factory fixture: make(is_admin=False, ...) -> committed User."""
    def _make(is_admin=False, username=None, email=None,
              password=DEFAULT_TEST_PASSWORD):
        return create_test_user(db_session, username, email, password, is_admin)
    return _make


@pytest.fixture()
def auth_headers(client):
    """Return a helper that logs a user in via the real /auth/login chain and
    yields an Authorization header. Going through login (rather than minting a
    JWT) keeps the real authentication path under test."""
    def _headers(user, password=DEFAULT_TEST_PASSWORD):
        r = client.post("/api/auth/login",
                        json={"username": user.username, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _headers


@pytest.fixture(autouse=True)
def _isolate_rate_limits():
    """Per-test isolation of the Redis rate-limit namespace.

    Clears all ``rl:*`` keys before each test so a per-IP bucket built up by
    one test never bleeds into the next. This is a TEST-ONLY fixture (it only
    runs under pytest against the test Redis DB) and changes no production
    default. No-op when Redis is unreachable.
    """
    try:
        import redis as _r
        cli = _r.from_url(os.environ["REDIS_URL"],
                          socket_connect_timeout=1, socket_timeout=1)
        for k in cli.scan_iter("rl:*"):
            cli.delete(k)
    except Exception:
        pass
    yield
