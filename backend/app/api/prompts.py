"""Prompt template management (admin only).

  GET  /api/prompts            - list all templates
  GET  /api/prompts/{id}       - view one
  PUT  /api/prompts/{id}       - edit (content / active flag)
  POST /api/prompts/{id}/clone - clone into a new version of the same key

Regular users have no access — every route is admin-gated. Editing a
template here changes what the AI pipeline runs on the next call (the
service always loads the active row for a key).
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, PromptTemplate
from app.schemas import PromptTemplateOut, PromptTemplateUpdate
from app.deps import require_admin
from app.services.audit import log_audit, snapshot

router = APIRouter(prefix="/prompts", tags=["prompts"])

_FIELDS = ["id", "key", "name", "version", "is_active"]


def _get_or_404(db: Session, pid: int) -> PromptTemplate:
    t = db.query(PromptTemplate).filter(PromptTemplate.id == pid).first()
    if not t:
        raise HTTPException(404, "prompt template not found")
    return t


@router.get("", response_model=List[PromptTemplateOut])
def list_prompts(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    return db.query(PromptTemplate).order_by(
        PromptTemplate.key, PromptTemplate.version.desc()
    ).all()


@router.get("/{pid}", response_model=PromptTemplateOut)
def get_prompt(pid: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    return _get_or_404(db, pid)


@router.put("/{pid}", response_model=PromptTemplateOut)
def update_prompt(pid: int, payload: PromptTemplateUpdate, request: Request,
                  db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    t = _get_or_404(db, pid)
    before = snapshot(t, _FIELDS)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    log_audit(db, admin, "prompt.update", target_type="prompt_template", target_id=t.id,
              before=before, after=snapshot(t, _FIELDS), request=request)
    return t


@router.post("/{pid}/clone", response_model=PromptTemplateOut)
def clone_prompt(pid: int, request: Request,
                 db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    src = _get_or_404(db, pid)
    last = db.query(PromptTemplate).filter(
        PromptTemplate.key == src.key
    ).order_by(PromptTemplate.version.desc()).first()
    next_version = (last.version + 1) if last else 1
    clone = PromptTemplate(
        key=src.key,
        name=src.name,
        version=next_version,
        system_prompt=src.system_prompt,
        user_prompt_template=src.user_prompt_template,
        output_schema=src.output_schema,
        is_active=False,   # new versions start inactive; activate explicitly
        created_by=admin.id,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    log_audit(db, admin, "prompt.clone", target_type="prompt_template", target_id=clone.id,
              after=snapshot(clone, _FIELDS), request=request)
    return clone
