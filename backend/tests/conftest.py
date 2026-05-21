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
