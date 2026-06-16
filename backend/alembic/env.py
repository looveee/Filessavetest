"""Alembic environment.

Reads DATABASE_URL from `app.config.settings` so we never duplicate
credentials between alembic.ini and the application.

Imports `app.models` so every ORM class registers itself on `Base.metadata`
before autogenerate inspects it.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------
# Path setup: env.py runs from backend/alembic/, but we want imports of
# `app.*` to resolve. Add the parent (backend/) to sys.path.
# ---------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ---------------------------------------------------------------------
# Application imports — these MUST come after the sys.path tweak.
# ---------------------------------------------------------------------
from app.config import settings           # noqa: E402
from app.database import Base             # noqa: E402

# Importing app.models registers every ORM class on Base.metadata.
# Without this, `--autogenerate` would diff against an empty schema.
import app.models                         # noqa: E402, F401

# ---------------------------------------------------------------------
# Standard alembic config plumbing
# ---------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the URL from settings into the alembic config object so both
# offline and online modes pick it up.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without an Engine — emits raw SQL.

    Useful for generating SQL files for DBAs to apply, or when the actual
    database isn't reachable from CI.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        # Postgres ENUMs need explicit handling in autogenerate
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations connected to a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
            # Render as_batch lets us survive on SQLite (test envs); harmless
            # on Postgres because the dialect ignores it for non-batch ops.
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


def _include_object(obj, name, type_, reflected, compare_to):
    """Filter out objects that should not be tracked by alembic.

    Currently no-op, but kept as the recommended hook so we can later
    exclude e.g. legacy materialized views without rewriting env.py.
    """
    return True


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
