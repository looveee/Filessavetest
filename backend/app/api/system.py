"""System self-check endpoints.

Visibility (v0.4):
  GET /api/system/health             - PUBLIC. Trivial liveness:
                                       {ok, version, app_name}. No internal info.
  GET /api/system/health-full        - ADMIN ONLY. Component checks. Sensitive
                                       fields (URLs, paths) only included when
                                       caller is admin.
  GET /api/system/permission-matrix  - ADMIN ONLY.
"""
import os
import time
import logging
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.celery_app import celery_app
from app.permissions import role_permission_matrix
from app.deps import require_admin

router = APIRouter(prefix="/system", tags=["system"])
log = logging.getLogger("system")


def _check_db(db: Session) -> dict:
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _check_redis() -> dict:
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL, socket_timeout=2)
        ok = r.ping()
        return {"ok": bool(ok)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _check_celery_broker() -> dict:
    out = {"ok": False}
    try:
        with celery_app.connection() as conn:
            conn.ensure_connection(max_retries=1, timeout=2)
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)[:200]
        return out
    try:
        replies = celery_app.control.ping(timeout=1) or []
        names = []
        for r in replies:
            if isinstance(r, dict):
                names.extend(r.keys())
        out["workers_active"] = len(names)
    except Exception:
        out["workers_active"] = 0
    return out


def _check_storage() -> dict:
    paths = {"uploads": settings.UPLOAD_DIR, "assets": settings.ASSETS_DIR}
    out = {}
    all_ok = True
    for name, p in paths.items():
        try:
            os.makedirs(p, exist_ok=True)
            test_file = os.path.join(p, ".healthcheck")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
            out[name] = {"writable": True}
        except Exception as e:
            all_ok = False
            out[name] = {"writable": False, "error": str(e)[:200]}
    return {"ok": all_ok, "dirs": out}


# ---------------------------------------------------------------------
#  PUBLIC — minimal liveness
# ---------------------------------------------------------------------
@router.get("/health")
def health():
    """Public liveness probe. Intentionally returns NO internal information."""
    return {
        "ok": True,
        "version": settings.APP_VERSION,
        "app_name": settings.APP_NAME,
    }


# ---------------------------------------------------------------------
#  ADMIN — component health (with sensitive details)
# ---------------------------------------------------------------------
@router.get("/health-full")
def health_full(db: Session = Depends(get_db),
                _admin = Depends(require_admin)):
    started = time.time()
    db_status = _check_db(db)
    redis_status = _check_redis()
    celery_status = _check_celery_broker()
    storage_status = _check_storage()

    all_ok = (
        db_status["ok"] and redis_status["ok"]
        and celery_status["ok"] and storage_status["ok"]
    )

    # Admin gets full diagnostic detail. We do NOT include broker URLs /
    # paths in the response unless this is an admin caller — gating already
    # ensures that, but we keep the variable name explicit.
    storage_with_paths = {
        "ok": storage_status["ok"],
        "dirs": {
            name: {**info, "path": (settings.UPLOAD_DIR if name == "uploads"
                                    else settings.ASSETS_DIR)}
            for name, info in storage_status["dirs"].items()
        },
    }
    redis_with_url = {**redis_status, "url": _safe_url(settings.REDIS_URL)}
    celery_with_broker = {**celery_status, "broker": _safe_url(settings.CELERY_BROKER_URL)}

    return {
        "ok": all_ok,
        "checked_at": int(time.time()),
        "took_ms": int((time.time() - started) * 1000),
        "components": {
            "database": db_status,
            "redis": redis_with_url,
            "celery_broker": celery_with_broker,
            "storage": storage_with_paths,
        },
        "ai_provider": settings.AI_PROVIDER,
        "ai": _ai_status(),
        "app_version": settings.APP_VERSION,
        "app_name": settings.APP_NAME,
        "rate_limit_enabled": settings.RATE_LIMIT_ENABLED,
    }


# ---------------------------------------------------------------------
#  ADMIN — permission matrix introspection
# ---------------------------------------------------------------------
@router.get("/permission-matrix")
def permission_matrix(_admin = Depends(require_admin)):
    return {"roles": role_permission_matrix()}


def _ai_status() -> dict:
    """Provider config summary — no secrets, safe for admin health view."""
    try:
        from app.services.ai import provider_status
        return provider_status()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


def _safe_url(u: str) -> str:
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(u)
        if parts.password:
            netloc = f"{parts.username}:***@{parts.hostname}"
            if parts.port:
                netloc += f":{parts.port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return u
    except Exception:
        return "<unparseable>"
