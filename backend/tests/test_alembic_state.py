"""Alembic state tests.

These are pure-static (no DB needed) checks that lock in the v0.5 contract:

  1. There is exactly one alembic head — i.e. the migration tree hasn't
     forked. A second head almost always means someone forgot to merge
     branches and prod will hit "Multiple head revisions" at upgrade time.

  2. backend/app/main.py contains NO `Base.metadata.create_all(...)` and
     NO `ALTER TABLE IF NOT EXISTS` strings. Schema management lives in
     alembic now and ONLY in alembic.

  3. (Optional) If a real DATABASE_URL is reachable, `alembic upgrade head`
     succeeds against an empty schema. Skipped automatically otherwise.
"""

import os
import re
import sys
import subprocess
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
BACKEND_DIR = HERE.parent
REPO_ROOT = BACKEND_DIR.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


# ---------------------------------------------------------------------
# 1. Single head invariant — purely from the versions/ directory
# ---------------------------------------------------------------------
def test_single_alembic_head():
    """Walk the migration files and prove there is exactly one head.

    We don't shell out to alembic for this — we just parse `revision` /
    `down_revision` from each file. A 'head' is a revision that nobody
    points to via down_revision.
    """
    versions_dir = BACKEND_DIR / "alembic" / "versions"
    assert versions_dir.is_dir(), f"missing migrations directory: {versions_dir}"

    revs = {}        # revision_id -> down_revision
    for path in versions_dir.glob("*.py"):
        if path.name.startswith("__"):
            continue
        text = path.read_text()
        rev_m = re.search(
            r"^revision(?::\s*[A-Za-z\[\], |]+)?\s*=\s*['\"]([^'\"]+)['\"]",
            text, flags=re.MULTILINE,
        )
        down_m = re.search(
            r"^down_revision(?::\s*[A-Za-z\[\], |]+)?\s*=\s*(?:['\"]([^'\"]+)['\"]|None)",
            text, flags=re.MULTILINE,
        )
        assert rev_m, f"could not parse revision from {path.name}"
        assert down_m, f"could not parse down_revision from {path.name}"
        revs[rev_m.group(1)] = down_m.group(1)  # may be None for the root

    pointed_to = {d for d in revs.values() if d}
    heads = [r for r in revs if r not in pointed_to]
    assert len(heads) == 1, (
        f"alembic should have exactly ONE head, found {len(heads)}: {heads}. "
        f"Branched migration tree — merge before shipping."
    )


# ---------------------------------------------------------------------
# 2. main.py must not perform schema bootstrap anymore
# ---------------------------------------------------------------------
def test_main_py_has_no_schema_bootstrap():
    main_py = BACKEND_DIR / "app" / "main.py"
    text = main_py.read_text()

    forbidden_substrings = [
        "create_all(",
        "ALTER TABLE IF NOT EXISTS",
        "_DEV_MIGRATIONS",
    ]
    found = [s for s in forbidden_substrings if s in text]
    assert not found, (
        f"main.py still contains forbidden schema-bootstrap patterns: {found}. "
        "Schema management is alembic-only since v0.5."
    )


# ---------------------------------------------------------------------
# 3. main.py imports — no leftover engine/Base/text from the bootstrap
# ---------------------------------------------------------------------
def test_main_py_no_orphan_db_imports():
    """If the bootstrap is gone, these imports should be gone too."""
    text = (BACKEND_DIR / "app" / "main.py").read_text()

    # We DO still import settings / startup_check / API routers — those
    # are fine. We just don't want unused engine/Base/text imports.
    bad_imports = [
        "from sqlalchemy import text",
        "from app.database import Base, engine",
        "from app.database import engine",
    ]
    for line in bad_imports:
        assert line not in text, (
            f"main.py still has the import `{line}` left over from the old "
            "schema-bootstrap path. Remove it."
        )


# ---------------------------------------------------------------------
# 4. Optional live test: alembic upgrade head against a real DB
# ---------------------------------------------------------------------
def _have_db(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text
        e = create_engine(url, pool_pre_ping=True)
        with e.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    "ALEMBIC_LIVE_TEST_DB_URL" not in os.environ,
    reason="set ALEMBIC_LIVE_TEST_DB_URL to a throwaway empty DB to enable",
)
def test_alembic_upgrade_head_live():
    """Run `alembic upgrade head` against a throwaway database.

    This is opt-in via env var so the test suite stays fast by default
    and doesn't require a Postgres instance just to verify static contracts.
    """
    url = os.environ["ALEMBIC_LIVE_TEST_DB_URL"]
    assert _have_db(url), f"DB not reachable: {url}"

    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env.setdefault("SECRET_KEY", "test-secret")

    proc = subprocess.run(
        ["alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"alembic upgrade head failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    # And `alembic current` should now show our single head
    proc2 = subprocess.run(
        ["alembic", "-c", str(ALEMBIC_INI), "current"],
        cwd=str(BACKEND_DIR), env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert proc2.returncode == 0, proc2.stderr
    assert proc2.stdout.strip(), "alembic current returned nothing — DB not stamped"
