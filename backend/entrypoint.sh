#!/bin/sh
# Backend container entrypoint.
#
# Runs `alembic upgrade head` (idempotent) before starting the API server.
# Worker container does NOT use this script — workers must not race on
# migrations.
#
# `set -e` ensures any migration failure kills the container so we never
# accept traffic against an out-of-date schema.

set -e

echo ">> entrypoint: applying migrations"
alembic upgrade head

echo ">> entrypoint: starting uvicorn"
# Pass through any CMD args from compose (e.g. --proxy-headers in prod)
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
