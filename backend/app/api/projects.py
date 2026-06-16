"""Project endpoints — CRUD, members, episodes, source text upload."""
import os
from typing import List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User, Project, ProjectMember, ProjectRole,
    SourceText, Episode,
)
from app.schemas import (
    ProjectCreate, ProjectUpdate, ProjectOut,
    MemberAdd, MemberOut, SourceTextOut, EpisodeOut,
)
from app.deps import get_current_user
from app.permissions import Permission, require_permission, has_permission
from app.services.audit import log_audit, snapshot
from app.config import settings
from app.utils.upload_safety import (
    validate_upload_filename, sanitize_display_filename, build_storage_filename,
)

router = APIRouter(prefix="/projects", tags=["projects"])

_PROJECT_FIELDS = ["id", "name", "description", "genre", "style", "target_episodes", "owner_id", "status"]
_MEMBER_FIELDS = ["id", "project_id", "user_id", "role"]


def _get_project_or_404(db: Session, project_id: int) -> Project:
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@router.get("", response_model=List[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Return projects the user owns OR is a member of (admins see everything)."""
    q = db.query(Project)
    if not user.is_admin:
        member_pids = [m.project_id for m in user.memberships]
        q = q.filter((Project.owner_id == user.id) | (Project.id.in_(member_pids)))
    return q.order_by(Project.id.desc()).all()


@router.post("", response_model=ProjectOut)
def create_project(payload: ProjectCreate, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    p = Project(**payload.model_dump(), owner_id=user.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    log_audit(
        db, user, "project.create",
        project_id=p.id, target_type="project", target_id=p.id,
        after=snapshot(p, _PROJECT_FIELDS),
        request=request,
    )
    return p


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    p = _get_project_or_404(db, project_id)
    require_permission(db, user, project_id, Permission.PROJECT_READ)
    return p


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectUpdate, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _get_project_or_404(db, project_id)
    require_permission(db, user, project_id, Permission.PROJECT_UPDATE)

    before = snapshot(p, _PROJECT_FIELDS)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    log_audit(
        db, user, "project.update",
        project_id=p.id, target_type="project", target_id=p.id,
        before=before, after=snapshot(p, _PROJECT_FIELDS),
        request=request,
    )
    return p


@router.delete("/{project_id}")
def delete_project(project_id: int, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    p = _get_project_or_404(db, project_id)
    require_permission(db, user, project_id, Permission.PROJECT_DELETE)
    before = snapshot(p, _PROJECT_FIELDS)
    db.delete(p)
    db.commit()
    log_audit(
        db, user, "project.delete",
        project_id=project_id, target_type="project", target_id=project_id,
        before=before, request=request,
    )
    return {"ok": True}


# -------- members --------

@router.get("/{project_id}/members", response_model=List[MemberOut])
def list_members(project_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    require_permission(db, user, project_id, Permission.PROJECT_READ)
    return db.query(ProjectMember).filter(ProjectMember.project_id == project_id).all()


@router.post("/{project_id}/members", response_model=MemberOut)
def add_or_update_member(project_id: int, payload: MemberAdd, request: Request,
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_permission(db, user, project_id, Permission.MEMBER_MANAGE)
    try:
        role_enum = ProjectRole(payload.role)
    except ValueError:
        raise HTTPException(400, f"invalid role {payload.role}")

    # Owner cannot be granted via members table — owner is always projects.owner_id.
    if role_enum == ProjectRole.owner:
        raise HTTPException(
            400,
            "owner role cannot be assigned via project_members; "
            "owner is always the project creator and cannot be transferred via this endpoint.",
        )

    target = db.query(User).filter(User.id == payload.user_id).first()
    if not target:
        raise HTTPException(404, "target user not found")

    # Cannot add the project owner as a member (they're already owner).
    proj = _get_project_or_404(db, project_id)
    if proj.owner_id == payload.user_id:
        raise HTTPException(400, "user is the project owner; no member row needed")

    existing = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == payload.user_id,
    ).first()
    if existing:
        before = snapshot(existing, _MEMBER_FIELDS)
        existing.role = role_enum
        db.commit()
        db.refresh(existing)
        log_audit(
            db, user, "member.role_update",
            project_id=project_id, target_type="member", target_id=existing.id,
            before=before, after=snapshot(existing, _MEMBER_FIELDS),
            request=request,
        )
        return existing

    m = ProjectMember(project_id=project_id, user_id=payload.user_id, role=role_enum)
    db.add(m)
    db.commit()
    db.refresh(m)
    log_audit(
        db, user, "member.add",
        project_id=project_id, target_type="member", target_id=m.id,
        after=snapshot(m, _MEMBER_FIELDS),
        request=request,
    )
    return m


@router.delete("/{project_id}/members/{user_id}")
def remove_member(project_id: int, user_id: int, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_permission(db, user, project_id, Permission.MEMBER_MANAGE)
    m = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
    ).first()
    if not m:
        raise HTTPException(404, "member not found")
    before = snapshot(m, _MEMBER_FIELDS)
    db.delete(m)
    db.commit()
    log_audit(
        db, user, "member.remove",
        project_id=project_id, target_type="member", target_id=before.get("id"),
        before=before, request=request,
    )
    return {"ok": True}


# -------- source text (novel upload) --------

@router.post("/{project_id}/source", response_model=SourceTextOut)
async def upload_source(project_id: int, request: Request,
                        file: UploadFile = File(...),
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    require_permission(db, user, project_id, Permission.SOURCE_UPLOAD)

    # ---- 1. validate filename / extension ----
    # Strategy A: reject path-like names outright (separators / NUL / .. /
    # absolute). Ordinary special chars are allowed and sanitized for display.
    raw_name = file.filename or ""
    validate_upload_filename(raw_name)
    display_name = sanitize_display_filename(raw_name)

    # ---- 2. validate / decode content ----
    raw = await file.read()
    if len(raw) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(413, "file too large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(400, "cannot decode file (try utf-8 or gbk)")
    # Reject content with NUL bytes — they are not legitimate in a novel
    # and tend to indicate the user uploaded a binary by mistake.
    if "\x00" in text:
        raise HTTPException(400, "file contains null bytes; not a text file")

    # ---- 3. write to disk under a guaranteed-safe path ----
    # The on-disk name is a UUID — the user's original filename is NEVER used
    # to build a path, so collisions and enumeration are impossible.
    upload_dir = os.path.realpath(settings.UPLOAD_DIR)
    os.makedirs(upload_dir, exist_ok=True)
    final_name = f"p{project_id}_{build_storage_filename(raw_name)}"
    path = os.path.realpath(os.path.join(upload_dir, final_name))
    # Belt + braces: confirm the resolved path is still under UPLOAD_DIR.
    if not (path == upload_dir or path.startswith(upload_dir + os.sep)):
        raise HTTPException(400, "invalid upload path")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    st = SourceText(
        project_id=project_id,
        filename=display_name,
        content=text,
        char_count=len(text),
        uploaded_by=user.id,
    )
    db.add(st)
    db.commit()
    db.refresh(st)
    log_audit(
        db, user, "source.upload",
        project_id=project_id, target_type="source_text", target_id=st.id,
        after={"filename": st.filename, "char_count": st.char_count, "path": path},
        request=request,
    )
    return st


@router.get("/{project_id}/source", response_model=List[SourceTextOut])
def list_source(project_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    require_permission(db, user, project_id, Permission.PROJECT_READ)
    return db.query(SourceText).filter(SourceText.project_id == project_id).all()


# -------- episodes --------

@router.get("/{project_id}/episodes", response_model=List[EpisodeOut])
def list_episodes(project_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    require_permission(db, user, project_id, Permission.EPISODE_READ)
    return db.query(Episode).filter(Episode.project_id == project_id) \
        .order_by(Episode.episode_number).all()
