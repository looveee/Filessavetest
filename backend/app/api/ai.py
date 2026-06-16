"""AI provider introspection, test, and run-log endpoints (v0.6).

  GET  /api/ai/providers  - any authed user. Current provider/model + which
                            providers are configured. NEVER returns API keys.
  POST /api/ai/test       - admin only. Fire a freeform prompt at the active
                            provider and return a sample output + latency.
  GET  /api/ai/runs       - scoped: admin sees all; others see runs for
                            projects they own or are a member of.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.models import User, Project, AIGenerationRun
from app.schemas import AITestRequest, AIRunOut
from app.deps import get_current_user, require_admin
from app.services.ai import ai_service, provider_status
from app.services.audit import log_audit

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/providers")
def get_providers(user: User = Depends(get_current_user)):
    """Provider config status. Safe for any authed user — booleans only."""
    return provider_status()


@router.post("/test")
def test_provider(payload: AITestRequest, request: Request,
                  db: Session = Depends(get_db),
                  admin: User = Depends(require_admin)):
    if not payload.prompt or not payload.prompt.strip():
        raise HTTPException(400, "prompt is required")
    resp = ai_service.raw_test(
        db, payload.prompt, system=payload.system or "",
        run_ctx={"created_by": admin.id},
    )
    log_audit(db, admin, "ai.test", target_type="ai", request=request,
              after={"provider": resp.provider, "model": resp.model,
                     "status": "completed" if resp.error is None else "failed"})
    return {
        "provider": resp.provider,
        "model": resp.model,
        "latency_ms": resp.latency_ms,
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
        "ok": resp.error is None,
        "error": resp.error,
        "sample_output": (resp.content or "")[:4000],
    }


def _allowed_project_ids(db: Session, user: User) -> List[int]:
    member_pids = [m.project_id for m in user.memberships]
    owned_pids = [p.id for p in db.query(Project.id).filter(Project.owner_id == user.id).all()]
    return list(set(member_pids + owned_pids))


@router.get("/runs", response_model=List[AIRunOut])
def list_runs(
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(AIGenerationRun)

    if not user.is_admin:
        allowed = _allowed_project_ids(db, user)
        # Visible: runs on an allowed project OR runs this user triggered
        # that have no project (e.g. their own /api/ai/test calls).
        if allowed:
            q = q.filter(or_(
                AIGenerationRun.project_id.in_(allowed),
                AIGenerationRun.created_by == user.id,
            ))
        else:
            q = q.filter(AIGenerationRun.created_by == user.id)

        if project_id is not None and project_id not in allowed:
            raise HTTPException(403, "you do not have access to this project's AI runs")

    if project_id is not None:
        q = q.filter(AIGenerationRun.project_id == project_id)
    if task_id is not None:
        q = q.filter(AIGenerationRun.task_id == task_id)
    if status_filter:
        q = q.filter(AIGenerationRun.status == status_filter)

    return q.order_by(AIGenerationRun.id.desc()).offset(offset).limit(limit).all()
