"""FastAPI application entrypoint.

Schema bootstrap is intentionally NOT performed here. The container
entrypoint (`backend/entrypoint.sh`) runs `alembic upgrade head` before
starting uvicorn. This module only:

  1. Validates production safety (refuses to boot with weak config when
     DEBUG=false).
  2. Ensures storage dirs exist on disk.
  3. Constructs the FastAPI app, CORS, optional /storage mount, and
     registers all routers.
"""
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.startup_check import validate_production_safety
from app.api import (
    auth as auth_api,
    users as users_api,
    projects as projects_api,
    tasks as tasks_api,
    episodes as episodes_api,
    reviews as reviews_api,
    workspace as workspace_api,
    assets_accounts,
    system as system_api,
    audit_logs as audit_logs_api,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("startup")

# ------------------------------------------------------------
# Production safety check — fails fast, before anything else.
# In DEBUG=true (dev) this is a no-op.
# ------------------------------------------------------------
validate_production_safety()

# ------------------------------------------------------------
# Storage dirs (filesystem-only; the schema lives in PostgreSQL and is
# managed by alembic from the container entrypoint, not here).
# ------------------------------------------------------------
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.ASSETS_DIR, exist_ok=True)

# ------------------------------------------------------------
# App
# ------------------------------------------------------------
app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    version=settings.APP_VERSION,
)

origins = ["*"] if settings.CORS_ORIGINS == "*" else [
    o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.SERVE_STORAGE_DIRECT:
    app.mount("/storage", StaticFiles(directory="/app/storage"), name="storage")
    log.info("SERVE_STORAGE_DIRECT=true — /storage is mounted (dev mode).")
else:
    log.info(
        "SERVE_STORAGE_DIRECT=false — /storage NOT mounted; "
        "use GET /api/assets/{id}/download for asset access."
    )


@app.get("/")
def root():
    return {
        "app": settings.APP_NAME,
        "status": "ok",
        "docs": "/docs",
        "version": settings.APP_VERSION,
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": settings.APP_VERSION}


PREFIX = settings.API_PREFIX
app.include_router(auth_api.router,         prefix=PREFIX)
app.include_router(users_api.router,        prefix=PREFIX)
app.include_router(projects_api.router,     prefix=PREFIX)
app.include_router(tasks_api.router,        prefix=PREFIX)
app.include_router(episodes_api.router,     prefix=PREFIX)
app.include_router(reviews_api.router,      prefix=PREFIX)
app.include_router(workspace_api.router,    prefix=PREFIX)
app.include_router(assets_accounts.assets_router,    prefix=PREFIX)
app.include_router(assets_accounts.accounts_router,  prefix=PREFIX)
app.include_router(assets_accounts.schedules_router, prefix=PREFIX)
app.include_router(system_api.router,       prefix=PREFIX)
app.include_router(audit_logs_api.router,   prefix=PREFIX)
