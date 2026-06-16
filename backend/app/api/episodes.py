"""Episode, script, and storyboard endpoints."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User, Episode, Script, Storyboard,
)
from app.schemas import (
    EpisodeOut, ScriptOut, StoryboardOut, StoryboardCreate,
)
from app.deps import get_current_user
from app.permissions import Permission, require_permission
from app.services.audit import log_audit, snapshot

router = APIRouter(prefix="/episodes", tags=["episodes"])

_SB_FIELDS = ["id", "episode_id", "shot_number", "duration_sec", "visual",
              "action", "voiceover", "subtitle", "sfx", "bgm"]


def _get_episode_or_404(db, ep_id):
    ep = db.query(Episode).filter(Episode.id == ep_id).first()
    if not ep:
        raise HTTPException(404, "episode not found")
    return ep


@router.get("/{episode_id}", response_model=EpisodeOut)
def get_episode(episode_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    ep = _get_episode_or_404(db, episode_id)
    require_permission(db, user, ep.project_id, Permission.EPISODE_READ)
    return ep


@router.get("/{episode_id}/scripts", response_model=List[ScriptOut])
def list_scripts(episode_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    ep = _get_episode_or_404(db, episode_id)
    require_permission(db, user, ep.project_id, Permission.EPISODE_READ)
    return db.query(Script).filter(Script.episode_id == episode_id) \
        .order_by(Script.version.desc()).all()


@router.get("/{episode_id}/storyboards", response_model=List[StoryboardOut])
def list_storyboards(episode_id: int, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    ep = _get_episode_or_404(db, episode_id)
    require_permission(db, user, ep.project_id, Permission.EPISODE_READ)
    return db.query(Storyboard).filter(Storyboard.episode_id == episode_id) \
        .order_by(Storyboard.shot_number).all()


@router.post("/{episode_id}/storyboards", response_model=StoryboardOut)
def create_storyboard(episode_id: int, payload: StoryboardCreate, request: Request,
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ep = _get_episode_or_404(db, episode_id)
    require_permission(db, user, ep.project_id, Permission.STORYBOARD_CREATE)
    sb = Storyboard(episode_id=episode_id, **payload.model_dump())
    db.add(sb); db.commit(); db.refresh(sb)
    log_audit(
        db, user, "storyboard.create",
        project_id=ep.project_id, target_type="storyboard", target_id=sb.id,
        after=snapshot(sb, _SB_FIELDS), request=request,
    )
    return sb


@router.patch("/storyboards/{sb_id}", response_model=StoryboardOut)
def update_storyboard(sb_id: int, payload: StoryboardCreate, request: Request,
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sb = db.query(Storyboard).filter(Storyboard.id == sb_id).first()
    if not sb:
        raise HTTPException(404, "storyboard not found")
    ep = _get_episode_or_404(db, sb.episode_id)
    require_permission(db, user, ep.project_id, Permission.STORYBOARD_UPDATE)
    before = snapshot(sb, _SB_FIELDS)
    for k, v in payload.model_dump().items():
        setattr(sb, k, v)
    db.commit(); db.refresh(sb)
    log_audit(
        db, user, "storyboard.update",
        project_id=ep.project_id, target_type="storyboard", target_id=sb.id,
        before=before, after=snapshot(sb, _SB_FIELDS), request=request,
    )
    return sb


@router.delete("/storyboards/{sb_id}")
def delete_storyboard(sb_id: int, request: Request,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    sb = db.query(Storyboard).filter(Storyboard.id == sb_id).first()
    if not sb:
        raise HTTPException(404, "storyboard not found")
    ep = _get_episode_or_404(db, sb.episode_id)
    require_permission(db, user, ep.project_id, Permission.STORYBOARD_UPDATE)
    before = snapshot(sb, _SB_FIELDS)
    db.delete(sb); db.commit()
    log_audit(
        db, user, "storyboard.delete",
        project_id=ep.project_id, target_type="storyboard", target_id=before.get("id"),
        before=before, request=request,
    )
    return {"ok": True}
