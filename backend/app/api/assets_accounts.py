"""Assets / accounts / schedules endpoints."""
import os
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User, Asset, Account, PublishSchedule, Project, PublishStatus, ProjectMember,
)
from app.schemas import (
    AssetOut, AssetDebugOut, AccountCreate, AccountOut, ScheduleCreate, ScheduleOut,
)
from app.deps import get_current_user, require_admin
from app.permissions import Permission, require_permission, has_permission
from app.services.audit import log_audit, snapshot
from app.config import settings

# ---- Assets ----
assets_router = APIRouter(prefix="/assets", tags=["assets"])

_ACCOUNT_FIELDS = ["id", "owner_id", "platform", "name", "persona", "domain", "publish_frequency"]
_SCHED_FIELDS = ["id", "project_id", "asset_id", "account_id", "scheduled_at",
                 "status", "title", "description", "tags", "published_at"]


def _public_asset(a: Asset) -> dict:
    """Build the public asset payload with no internal paths."""
    return {
        "id": a.id,
        "project_id": a.project_id,
        "episode_id": a.episode_id,
        "asset_type": a.asset_type,
        "duration_sec": a.duration_sec,
        "status": a.status,
        "created_at": a.created_at,
        "download_url": f"/api/assets/{a.id}/download",
    }


@assets_router.get("", response_model=List[AssetOut])
def list_assets(project_id: Optional[int] = None,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    q = db.query(Asset)
    if not user.is_admin:
        member_pids = [m.project_id for m in user.memberships]
        owned_pids = [p.id for p in db.query(Project.id).filter(Project.owner_id == user.id).all()]
        allowed = list(set(member_pids + owned_pids))
        q = q.filter(Asset.project_id.in_(allowed))
    if project_id:
        require_permission(db, user, project_id, Permission.ASSET_READ)
        q = q.filter(Asset.project_id == project_id)
    rows = q.order_by(Asset.id.desc()).limit(500).all()
    return [_public_asset(a) for a in rows]


@assets_router.get("/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    a = db.query(Asset).filter(Asset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "asset not found")
    require_permission(db, user, a.project_id, Permission.ASSET_READ)
    return _public_asset(a)


@assets_router.get("/{asset_id}/debug", response_model=AssetDebugOut)
def get_asset_debug(asset_id: int,
                    db: Session = Depends(get_db),
                    _admin: User = Depends(require_admin)):
    """Admin-only diagnostic view exposing the internal file_path,
    cover_path and meta. Use sparingly — these strings reveal disk
    layout and should never reach a non-admin user."""
    a = db.query(Asset).filter(Asset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "asset not found")
    return a


@assets_router.get("/{asset_id}/download")
def download_asset(asset_id: int, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Authenticated download of an asset's video file.

    Required permission: `asset.read` on the project. Outsiders → 403.
    Path containment: the resolved file path must live under ASSETS_DIR
    OR UPLOAD_DIR; otherwise we treat it as a misconfiguration and 500.
    """
    a = db.query(Asset).filter(Asset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "asset not found")
    require_permission(db, user, a.project_id, Permission.ASSET_READ)

    if not a.file_path:
        raise HTTPException(404, "asset has no file_path")

    # Allow either a path that lives in ASSETS_DIR (mock provider writes
    # there) or in UPLOAD_DIR (uncommon, but legal). Reject anything else
    # to defeat path traversal even if file_path was somehow tampered.
    real = os.path.realpath(a.file_path)
    allowed_roots = [
        os.path.realpath(settings.ASSETS_DIR),
        os.path.realpath(settings.UPLOAD_DIR),
    ]
    inside = any(real == root or real.startswith(root + os.sep)
                 for root in allowed_roots)
    if not inside:
        # Don't leak the actual paths to the client.
        log_audit(
            db, user, "asset.download_blocked_out_of_root",
            project_id=a.project_id, target_type="asset", target_id=a.id,
            after={"realpath_redacted": True}, request=request,
        )
        raise HTTPException(500, "asset path is outside allowed roots")

    if not os.path.isfile(real):
        raise HTTPException(404, "asset file missing on disk")

    log_audit(
        db, user, "asset.download",
        project_id=a.project_id, target_type="asset", target_id=a.id,
        request=request,
    )

    filename = os.path.basename(real)
    # Pick a sensible content-type for video; FileResponse will sniff if we
    # don't set one. Mock outputs are .mp4-shaped.
    return FileResponse(
        real,
        filename=filename,
        media_type="video/mp4" if filename.lower().endswith(".mp4") else None,
    )


# ---- Accounts ----
accounts_router = APIRouter(prefix="/accounts", tags=["accounts"])


@accounts_router.get("", response_model=List[AccountOut])
def list_accounts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Account)
    if not user.is_admin:
        q = q.filter(Account.owner_id == user.id)
    return q.order_by(Account.id.desc()).all()


@accounts_router.get("/usable", response_model=List[AccountOut])
def list_usable_accounts(project_id: int,
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """Accounts the current user may use to create a schedule on this project.

    Rule (consistent with POST /schedules):
      - admin: all accounts that belong to anyone on the project
               (own + project owner + project members' accounts)
      - non-admin: own accounts + project owner's accounts

    Required permission: `account.use` on this project (publishers,
    managers, owners, admins). Returns AccountOut which intentionally
    excludes any credential field.
    """
    require_permission(db, user, project_id, Permission.ACCOUNT_USE)

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "project not found")

    if user.is_admin:
        # Admin sees: own + owner's + members' accounts
        member_uids = [m.user_id for m in db.query(ProjectMember)
                       .filter_by(project_id=project_id).all()]
        owner_set = set(member_uids + [project.owner_id, user.id])
    else:
        owner_set = {user.id, project.owner_id}

    rows = db.query(Account).filter(Account.owner_id.in_(list(owner_set))).order_by(
        Account.id.desc()
    ).all()
    return rows


@accounts_router.post("", response_model=AccountOut)
def create_account(payload: AccountCreate, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    # account.create is a user-level permission (no project context).
    require_permission(db, user, None, Permission.ACCOUNT_CREATE)
    a = Account(owner_id=user.id, **payload.model_dump())
    db.add(a); db.commit(); db.refresh(a)
    log_audit(
        db, user, "account.create",
        target_type="account", target_id=a.id,
        after=snapshot(a, _ACCOUNT_FIELDS), request=request,
    )
    return a


@accounts_router.delete("/{account_id}")
def delete_account(account_id: int, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    a = db.query(Account).filter(Account.id == account_id).first()
    if not a:
        raise HTTPException(404, "account not found")
    if a.owner_id != user.id and not user.is_admin:
        raise HTTPException(403, "not yours")
    before = snapshot(a, _ACCOUNT_FIELDS)
    db.delete(a); db.commit()
    log_audit(
        db, user, "account.delete",
        target_type="account", target_id=account_id,
        before=before, request=request,
    )
    return {"ok": True}


# ---- Schedules ----
schedules_router = APIRouter(prefix="/schedules", tags=["schedules"])


@schedules_router.get("", response_model=List[ScheduleOut])
def list_schedules(status: Optional[str] = None,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    q = db.query(PublishSchedule)
    if not user.is_admin:
        member_pids = [m.project_id for m in user.memberships]
        owned_pids = [p.id for p in db.query(Project.id).filter(Project.owner_id == user.id).all()]
        allowed = list(set(member_pids + owned_pids))
        q = q.filter(PublishSchedule.project_id.in_(allowed))
    if status:
        q = q.filter(PublishSchedule.status == status)
    return q.order_by(PublishSchedule.scheduled_at.desc()).limit(500).all()


@schedules_router.post("", response_model=ScheduleOut)
def create_schedule(payload: ScheduleCreate, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    asset = db.query(Asset).filter(Asset.id == payload.asset_id).first()
    if not asset:
        raise HTTPException(404, "asset not found")

    # Need both schedule.create AND account.use on the project context
    require_permission(db, user, asset.project_id, Permission.SCHEDULE_CREATE)
    require_permission(db, user, asset.project_id, Permission.ACCOUNT_USE)

    account = db.query(Account).filter(Account.id == payload.account_id).first()
    if not account:
        raise HTTPException(404, "account not found")
    # Account ownership rule (v0.3):
    #   - admin: any account
    #   - account owner: their own
    #   - project members: may also use the *project owner's* accounts.
    #     This lets a dedicated `publisher` role schedule with the studio
    #     account that lives on the project owner's user record.
    project = db.query(Project).filter(Project.id == asset.project_id).first()
    project_owner_id = project.owner_id if project else None
    allowed_account_owners = {user.id}
    if project_owner_id is not None:
        allowed_account_owners.add(project_owner_id)
    if account.owner_id not in allowed_account_owners and not user.is_admin:
        raise HTTPException(
            403,
            "you may only schedule with an account you own or one owned by the project owner",
        )

    s = PublishSchedule(
        project_id=asset.project_id,
        asset_id=payload.asset_id,
        account_id=payload.account_id,
        scheduled_at=payload.scheduled_at,
        title=payload.title,
        description=payload.description,
        tags=payload.tags,
        status=PublishStatus.pending,
        created_by=user.id,
    )
    db.add(s); db.commit(); db.refresh(s)
    log_audit(
        db, user, "schedule.create",
        project_id=s.project_id, target_type="schedule", target_id=s.id,
        after=snapshot(s, _SCHED_FIELDS), request=request,
    )
    return s


@schedules_router.patch("/{sid}/status", response_model=ScheduleOut)
def update_schedule_status(sid: int, status: str, request: Request,
                           db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    s = db.query(PublishSchedule).filter(PublishSchedule.id == sid).first()
    if not s:
        raise HTTPException(404, "schedule not found")
    require_permission(db, user, s.project_id, Permission.SCHEDULE_UPDATE)
    try:
        st = PublishStatus(status)
    except ValueError:
        raise HTTPException(400, f"invalid status {status}")
    before = snapshot(s, _SCHED_FIELDS)
    s.status = st
    if st == PublishStatus.published:
        s.published_at = datetime.utcnow()
    db.commit(); db.refresh(s)
    log_audit(
        db, user, "schedule.status_update",
        project_id=s.project_id, target_type="schedule", target_id=s.id,
        before=before, after=snapshot(s, _SCHED_FIELDS), request=request,
    )
    return s
