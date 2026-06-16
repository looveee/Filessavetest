"""'My Workspace' aggregated endpoint."""
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from app.database import get_db
from app.models import (
    User, Project, GenerationTask, TaskStatus, TaskType,
    PublishSchedule, PublishStatus, Asset, AIGenerationRun,
)
from app.deps import get_current_user

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("")
def my_workspace(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 1. projects I own
    my_projects = db.query(Project).filter(Project.owner_id == user.id) \
        .order_by(Project.id.desc()).limit(20).all()

    # 2. tasks assigned to me OR pending in projects I have a role in (waiting human)
    member_pids = [m.project_id for m in user.memberships]
    owned_pids = [p.id for p in my_projects]
    allowed_pids = list(set(member_pids + owned_pids))

    tasks_for_me = db.query(GenerationTask).filter(
        or_(
            GenerationTask.assigned_to == user.id,
            (GenerationTask.project_id.in_(allowed_pids)) &
            (GenerationTask.status == TaskStatus.waiting_human),
        )
    ).order_by(GenerationTask.id.desc()).limit(50).all()

    # 3. videos waiting my review
    review_tasks = db.query(GenerationTask).filter(
        GenerationTask.project_id.in_(allowed_pids),
        GenerationTask.status == TaskStatus.in_review,
    ).order_by(GenerationTask.id.desc()).limit(50).all()

    # 4. schedules I created or in projects I belong to with pending status
    pending_publish = db.query(PublishSchedule).filter(
        PublishSchedule.project_id.in_(allowed_pids),
        PublishSchedule.status == PublishStatus.pending,
    ).order_by(PublishSchedule.scheduled_at).limit(50).all()

    # 5. AI usage today (calls + tokens) for projects I can see, aggregated
    #    from ai_generation_runs.
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    ai_today_q = db.query(
        func.count(AIGenerationRun.id),
        func.coalesce(func.sum(AIGenerationRun.input_tokens), 0),
        func.coalesce(func.sum(AIGenerationRun.output_tokens), 0),
    ).filter(AIGenerationRun.created_at >= day_start)
    if not user.is_admin:
        if allowed_pids:
            ai_today_q = ai_today_q.filter(or_(
                AIGenerationRun.project_id.in_(allowed_pids),
                AIGenerationRun.created_by == user.id,
            ))
        else:
            ai_today_q = ai_today_q.filter(AIGenerationRun.created_by == user.id)
    ai_calls_today, ai_in_tokens_today, ai_out_tokens_today = ai_today_q.one()

    def _proj_name(pid):
        p = next((x for x in my_projects if x.id == pid), None)
        if p:
            return p.name
        row = db.query(Project.name).filter(Project.id == pid).first()
        return row[0] if row else f"Project {pid}"

    def _task_card(t):
        return {
            "id": t.id,
            "project_id": t.project_id,
            "project_name": _proj_name(t.project_id),
            "episode_id": t.episode_id,
            "task_type": t.task_type.value if hasattr(t.task_type, "value") else t.task_type,
            "status": t.status.value if hasattr(t.status, "value") else t.status,
            "ai_score": t.ai_score,
            "risk_flags": t.risk_flags or [],
            "assigned_to": t.assigned_to,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    return {
        "my_projects": [
            {"id": p.id, "name": p.name, "status": p.status, "genre": p.genre}
            for p in my_projects
        ],
        "tasks_for_me": [_task_card(t) for t in tasks_for_me],
        "review_queue": [_task_card(t) for t in review_tasks],
        "publish_queue": [
            {
                "id": s.id, "project_id": s.project_id,
                "project_name": _proj_name(s.project_id),
                "asset_id": s.asset_id, "account_id": s.account_id,
                "scheduled_at": s.scheduled_at.isoformat(),
                "status": s.status.value if hasattr(s.status, "value") else s.status,
                "title": s.title,
            }
            for s in pending_publish
        ],
        "summary": {
            "owned_projects": len(my_projects),
            "tasks_pending": len(tasks_for_me),
            "review_pending": len(review_tasks),
            "publish_pending": len(pending_publish),
            "ai_calls_today": int(ai_calls_today or 0),
            "ai_tokens_today": int((ai_in_tokens_today or 0) + (ai_out_tokens_today or 0)),
        },
    }
