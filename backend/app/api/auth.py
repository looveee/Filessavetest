"""Auth endpoints: register, login, me. Rate-limited via Redis."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserLogin, UserOut, TokenOut
from app.security import hash_password, verify_password, create_access_token
from app.deps import get_current_user
from app.services.audit import log_audit
from app.services import ratelimit
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _rl_or_audit(db: Session, request: Request, *, bucket: str, limit: int,
                 window: int, extra_key: str = None, action: str = "auth.rate_limited"):
    """Apply a rate-limit bucket. If exceeded, write an audit row first
    (so abuse is observable in /api/audit-logs) then raise 429."""
    try:
        ratelimit.enforce(
            request=request, bucket=bucket, limit=limit,
            window_seconds=window, extra_key=extra_key,
        )
    except HTTPException as e:
        if e.status_code == 429:
            log_audit(
                db, None, action,
                target_type="auth",
                after={"bucket": bucket, "extra_key_redacted": bool(extra_key)},
                request=request, commit=True,
            )
        raise


@router.post("/register", response_model=TokenOut)
def register(payload: UserCreate, request: Request, db: Session = Depends(get_db)):
    _rl_or_audit(
        db, request,
        bucket=ratelimit.register_ip_key(),
        limit=settings.RL_REGISTER_IP_LIMIT,
        window=settings.RL_REGISTER_IP_WINDOW,
        action="auth.rate_limited",
    )

    exists = db.query(User).filter(
        or_(User.email == payload.email, User.username == payload.username)
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="Email or username already taken")
    is_first = db.query(User).count() == 0
    user = User(
        email=payload.email,
        username=payload.username,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        is_admin=is_first,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_audit(
        db, user, "user.register",
        target_type="user", target_id=user.id,
        after={
            "username": user.username,
            "email": user.email,
            "is_admin": user.is_admin,
        },
        request=request,
    )

    token = create_access_token(user.id)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)):
    # Per-IP throttle on every attempt — cheap blanket protection.
    _rl_or_audit(
        db, request,
        bucket=ratelimit.login_ip_key(),
        limit=settings.RL_LOGIN_IP_LIMIT,
        window=settings.RL_LOGIN_IP_WINDOW,
    )
    # NOTE: per-username throttle is intentionally *not* applied here.
    # We only count it on FAILED attempts below, so a user who keeps typing
    # their correct password isn't progressively locked out by their own
    # successful logins.

    user = db.query(User).filter(
        or_(User.email == payload.username, User.username == payload.username)
    ).first()
    bad_password = bool(user) and not verify_password(payload.password, user.hashed_password)

    if not user or bad_password:
        # Count this failure against the per-username bucket. If we've now
        # exhausted that bucket, return 429 (still with a generic message
        # so the attacker can't tell whether the account exists). Otherwise
        # return the standard 401.
        try:
            ratelimit.enforce(
                request=request,
                bucket=ratelimit.login_user_key(),
                limit=settings.RL_LOGIN_USER_LIMIT,
                window_seconds=settings.RL_LOGIN_USER_WINDOW,
                extra_key=payload.username.lower(),
            )
        except HTTPException as e:
            if e.status_code == 429:
                log_audit(
                    db, None, "auth.rate_limited",
                    target_type="auth",
                    after={"bucket": ratelimit.login_user_key(),
                           "username_len": len(payload.username or "")},
                    request=request,
                )
            # Re-raise as the 429 — generic detail (HTTPException's default
            # detail is informative but not "user exists"); we override to
            # match login's generic shape so timing/error don't leak account
            # existence.
            raise HTTPException(status_code=429,
                                detail="Too many requests. Please slow down.",
                                headers={"Retry-After": e.headers.get("Retry-After", "60")})

        log_audit(
            db, None, "user.login_failed",
            target_type="user",
            after={"identifier_len": len(payload.username or "")},
            request=request,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        log_audit(
            db, user, "user.login_disabled",
            target_type="user", target_id=user.id, request=request,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    log_audit(
        db, user, "user.login",
        target_type="user", target_id=user.id,
        after={"username": user.username},
        request=request,
    )
    token = create_access_token(user.id)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
