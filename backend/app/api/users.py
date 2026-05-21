"""User management endpoints.

Security model (v0.3+):
  - GET /users           : admin -> all; non-admin -> self only (single-element list)
  - GET /users/{id}      : admin -> any; non-admin -> only their own id
  - GET /users/lookup    : exact-match username search, returns UserPublic only
                            (id / username / full_name). Used to add members.
                            Rate-limited to prevent enumeration.
  - PATCH .../active     : admin only
  - PATCH .../admin      : admin only
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import UserOut, UserPublic
from app.deps import get_current_user, require_admin
from app.services import ratelimit
from app.services.audit import log_audit
from app.config import settings

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """Admin sees the full directory. Non-admin only sees themselves.

    This deliberately does NOT 403 for non-admin — returning [self] keeps
    the existing UI page (which lists users) functional in read-only mode.
    """
    if user.is_admin:
        return db.query(User).order_by(User.id).all()
    return [user]


@router.get("/lookup", response_model=List[UserPublic])
def lookup_users(
    request: Request,
    username: str = Query(..., min_length=2, max_length=64,
                          description="Exact username to look up"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Exact-match username search for adding members.

    - Returns at most one result (username is unique).
    - UserPublic strips email / is_admin / is_active / created_at.
    - Inactive users are excluded.
    - Available to any logged-in user (you need to know the exact username).
    - Rate limited per IP to prevent enumeration.
    """
    try:
        ratelimit.enforce(
            request=request,
            bucket=ratelimit.lookup_ip_key(),
            limit=settings.RL_LOOKUP_IP_LIMIT,
            window_seconds=settings.RL_LOOKUP_IP_WINDOW,
        )
    except HTTPException as e:
        if e.status_code == 429:
            log_audit(
                db, user, "user.lookup_rate_limited",
                target_type="user",
                after={"username_len": len(username)},  # never log the literal needle
                request=request,
            )
        raise

    target = db.query(User).filter(
        User.username == username,
        User.is_active == True,
    ).first()
    return [target] if target else []


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    if not user.is_admin and user.id != user_id:
        raise HTTPException(403, "you can only view your own profile")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "user not found")
    return u


@router.patch("/{user_id}/active", response_model=UserOut)
def set_active(user_id: int, active: bool, db: Session = Depends(get_db),
               _: User = Depends(require_admin)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "user not found")
    u.is_active = active
    db.commit()
    db.refresh(u)
    return u


@router.patch("/{user_id}/admin", response_model=UserOut)
def set_admin(user_id: int, admin: bool, db: Session = Depends(get_db),
              _: User = Depends(require_admin)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "user not found")
    u.is_admin = admin
    db.commit()
    db.refresh(u)
    return u
