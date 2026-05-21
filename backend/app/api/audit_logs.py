"""Audit log query endpoint.

Visibility rules (v0.3):
  - admin: all logs
  - project owner: logs of their projects + their own user-level actions
  - regular member: only logs of projects they are a member of (or owner of)
                    + their own user-level actions
  - logs with no project_id (login/register/account.*) are visible to:
      * admin (all)
      * the user who performed them (own user-level actions)

Filters: action (substring), project_id, user_id, since, until, limit, offset.
"""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from app.database import get_db
from app.models import User, Project, ProjectMember, AuditLog
from app.schemas import AuditLogOut
from app.deps import get_current_user

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])


def _allowed_project_ids(db: Session, user: User) -> List[int]:
    """Projects the user can read (excluding admin override; caller handles admin)."""
    member_pids = [m.project_id for m in user.memberships]
    owned_pids = [
        p.id for p in db.query(Project.id).filter(Project.owner_id == user.id).all()
    ]
    return list(set(member_pids + owned_pids))


@router.get("", response_model=List[AuditLogOut])
def list_audit_logs(
    action: Optional[str] = Query(None, description="Substring match on action"),
    project_id: Optional[int] = Query(None),
    user_id: Optional[int] = Query(None, description="filter by actor"),
    since: Optional[datetime] = Query(None, description="ISO datetime, inclusive"),
    until: Optional[datetime] = Query(None, description="ISO datetime, exclusive"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(AuditLog)

    # ---- visibility scope ----
    if not user.is_admin:
        allowed_pids = _allowed_project_ids(db, user)
        # Visible rows:
        #   - any audit log on an allowed project, OR
        #   - logs without project_id but performed BY this user
        if allowed_pids:
            q = q.filter(or_(
                AuditLog.project_id.in_(allowed_pids),
                and_(AuditLog.project_id.is_(None), AuditLog.user_id == user.id),
            ))
        else:
            q = q.filter(and_(AuditLog.project_id.is_(None), AuditLog.user_id == user.id))

        # Non-admin cannot ask for arbitrary project_id outside scope
        if project_id is not None and project_id not in allowed_pids:
            raise HTTPException(403, "you do not have access to this project's audit logs")

        # Non-admin cannot pivot on someone else's user_id
        if user_id is not None and user_id != user.id:
            raise HTTPException(403, "you can only filter by your own user_id")

    # ---- filters ----
    if action:
        q = q.filter(AuditLog.action.ilike(f"%{action}%"))
    if project_id is not None:
        q = q.filter(AuditLog.project_id == project_id)
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)
    if since is not None:
        q = q.filter(AuditLog.created_at >= since)
    if until is not None:
        q = q.filter(AuditLog.created_at < until)

    return q.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
