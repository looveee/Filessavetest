"""Audit log writer.

Single entrypoint `log_audit()` so every API handler writes a consistent row.
Field mapping (per spec):
  - user_id, project_id, task_id
  - action          : short verb, e.g. "project.create", "review.approve"
  - target_type     : "project" | "user" | "task" | "member" | "account" ...
  - target_id
  - before_value    : JSON snapshot before the change (None if N/A)
  - after_value     : JSON snapshot after the change (None if N/A)
  - ip              : derived from request, X-Forwarded-For aware
  - created_at      : auto

Failure mode: audit MUST NOT break business logic. Any exception from this
helper is swallowed and logged to stderr.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from ..models import AuditLog, User

log = logging.getLogger("audit")


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _safe_json(v: Any) -> Any:
    """Make sure a value is JSON-serialisable. Falls back to repr."""
    if v is None:
        return None
    try:
        import json
        json.dumps(v, default=str)
        return v
    except Exception:
        return repr(v)


def snapshot(obj: Any, fields: list) -> dict:
    """Pick a subset of attributes off an ORM object → dict (for before/after)."""
    if obj is None:
        return None
    out = {}
    for f in fields:
        try:
            v = getattr(obj, f, None)
            # enums → their .value
            v = getattr(v, "value", v)
            out[f] = _safe_json(v)
        except Exception:
            out[f] = None
    return out


def log_audit(
    db: Session,
    user: Optional[User],
    action: str,
    *,
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    before: Any = None,
    after: Any = None,
    request: Optional[Request] = None,
    commit: bool = True,
) -> Optional[AuditLog]:
    try:
        entry = AuditLog(
            user_id=user.id if user else None,
            project_id=project_id,
            task_id=task_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before_value=_safe_json(before),
            after_value=_safe_json(after),
            ip=_client_ip(request),
        )
        db.add(entry)
        if commit:
            db.commit()
        return entry
    except Exception as e:
        log.exception("audit log failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return None
