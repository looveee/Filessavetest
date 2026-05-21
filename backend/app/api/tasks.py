"""Tasks endpoints — create AI generation tasks, list, update, retry."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User, GenerationTask, TaskType, TaskStatus, Project, Episode,
)
from app.schemas import TaskCreate, TaskUpdate, TaskOut
from app.deps import get_current_user
from app.permissions import Permission, require_permission, has_permission
from app.services.audit import log_audit, snapshot
from app.tasks.ai_tasks import run_generation_task

router = APIRouter(prefix="/tasks", tags=["tasks"])

_TASK_FIELDS = ["id", "project_id", "episode_id", "task_type", "status",
                "owner_id", "created_by", "assigned_to", "ai_score", "error"]


def _get_task_or_404(db: Session, task_id: int) -> GenerationTask:
    t = db.query(GenerationTask).filter(GenerationTask.id == task_id).first()
    if not t:
        raise HTTPException(404, "task not found")
    return t


def _validate_assignee(db: Session, project: Project, assignee_id: int) -> None:
    """assigned_to must be the project owner, a project member, or a system admin.
    Anything else 400s."""
    target = db.query(User).filter(User.id == assignee_id).first()
    if not target:
        raise HTTPException(400, f"assigned_to user {assignee_id} not found")
    if not target.is_active:
        raise HTTPException(400, f"assigned_to user {assignee_id} is disabled")
    if target.is_admin:
        return
    if project.owner_id == assignee_id:
        return
    from app.models import ProjectMember
    is_member = db.query(ProjectMember).filter(
        ProjectMember.project_id == project.id,
        ProjectMember.user_id == assignee_id,
    ).first()
    if not is_member:
        raise HTTPException(
            400,
            f"assigned_to user {assignee_id} is neither project owner, member, nor admin",
        )


@router.post("", response_model=TaskOut)
def create_task(payload: TaskCreate, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == payload.project_id).first()
    if not project:
        raise HTTPException(404, "project not found")
    require_permission(db, user, project.id, Permission.TASK_CREATE)

    try:
        ttype = TaskType(payload.task_type)
    except ValueError:
        raise HTTPException(400, f"invalid task_type {payload.task_type}")
    if payload.episode_id:
        ep = db.query(Episode).filter(
            Episode.id == payload.episode_id,
            Episode.project_id == payload.project_id,
        ).first()
        if not ep:
            raise HTTPException(404, "episode not found in project")

    # If client tries to pre-assign at create time, that requires task.assign
    if payload.assigned_to and payload.assigned_to != user.id:
        require_permission(db, user, project.id, Permission.TASK_ASSIGN)
    if payload.assigned_to:
        _validate_assignee(db, project, payload.assigned_to)

    task = GenerationTask(
        project_id=project.id,
        episode_id=payload.episode_id,
        task_type=ttype,
        status=TaskStatus.pending,
        owner_id=project.owner_id,
        created_by=user.id,
        assigned_to=payload.assigned_to,
        input_data=payload.input_data or {},
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    log_audit(
        db, user, "task.create",
        project_id=task.project_id, task_id=task.id,
        target_type="task", target_id=task.id,
        after=snapshot(task, _TASK_FIELDS),
        request=request,
    )

    run_generation_task.delay(task.id)
    return task


@router.get("", response_model=List[TaskOut])
def list_tasks(
    project_id: Optional[int] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    assigned_to_me: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(GenerationTask)
    if not user.is_admin:
        member_pids = [m.project_id for m in user.memberships]
        owned_pids = [p.id for p in db.query(Project.id).filter(Project.owner_id == user.id).all()]
        allowed = list(set(member_pids + owned_pids))
        q = q.filter(GenerationTask.project_id.in_(allowed))
    if project_id:
        q = q.filter(GenerationTask.project_id == project_id)
    if status_filter:
        q = q.filter(GenerationTask.status == status_filter)
    if assigned_to_me:
        q = q.filter(GenerationTask.assigned_to == user.id)
    return q.order_by(GenerationTask.id.desc()).limit(500).all()


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    t = _get_task_or_404(db, task_id)
    require_permission(db, user, t.project_id, Permission.PROJECT_READ)
    return t


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate, request: Request,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = _get_task_or_404(db, task_id)
    data = payload.model_dump(exclude_unset=True)
    before = snapshot(t, _TASK_FIELDS)

    # Re-assignment is the privileged path
    if "assigned_to" in data and data["assigned_to"] != t.assigned_to:
        require_permission(db, user, t.project_id, Permission.TASK_ASSIGN)
        if data["assigned_to"] is not None:
            project = db.query(Project).filter(Project.id == t.project_id).first()
            if project:
                _validate_assignee(db, project, data["assigned_to"])
        action = "task.assign"
    else:
        require_permission(db, user, t.project_id, Permission.TASK_CREATE)
        action = "task.update"

    if "status" in data:
        try:
            data["status"] = TaskStatus(data["status"])
        except ValueError:
            raise HTTPException(400, f"invalid status {data['status']}")

    for k, v in data.items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)

    log_audit(
        db, user, action,
        project_id=t.project_id, task_id=t.id,
        target_type="task", target_id=t.id,
        before=before, after=snapshot(t, _TASK_FIELDS),
        request=request,
    )
    return t


@router.post("/{task_id}/retry", response_model=TaskOut)
def retry_task(task_id: int, request: Request,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    t = _get_task_or_404(db, task_id)
    require_permission(db, user, t.project_id, Permission.TASK_RETRY)
    before = snapshot(t, _TASK_FIELDS)
    t.status = TaskStatus.pending
    t.error = None
    db.commit()
    db.refresh(t)
    log_audit(
        db, user, "task.retry",
        project_id=t.project_id, task_id=t.id,
        target_type="task", target_id=t.id,
        before=before, after=snapshot(t, _TASK_FIELDS),
        request=request,
    )
    run_generation_task.delay(t.id)
    return t
