"""Review actions on tasks — approve / reject / rewrite / enhance_* / redo."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User, GenerationTask, TaskStatus, TaskType, Script,
)
from app.schemas import ReviewAction, TaskOut
from app.deps import get_current_user
from app.permissions import Permission, require_permission
from app.services.audit import log_audit, snapshot
from app.tasks.ai_tasks import run_generation_task
from app.services.ai_service import ai_service

router = APIRouter(prefix="/reviews", tags=["reviews"])

# action -> (audit_action_string, required_permission)
_ACTION_PERMS = {
    "approve":              ("review.approve",            Permission.REVIEW_APPROVE),
    "reject":               ("review.reject",             Permission.REVIEW_REJECT),
    "redo":                 ("review.redo",               Permission.REVIEW_REWRITE),
    "rewrite":              ("review.rewrite",            Permission.REVIEW_REWRITE),
    "enhance_conflict":     ("review.enhance_conflict",   Permission.REVIEW_REWRITE),
    "enhance_cliffhanger":  ("review.enhance_cliffhanger",Permission.REVIEW_REWRITE),
    "enhance_hook":         ("review.enhance_hook",       Permission.REVIEW_REWRITE),
}

_TASK_FIELDS = ["id", "project_id", "episode_id", "task_type", "status", "ai_score", "error"]


@router.post("/tasks/{task_id}", response_model=TaskOut)
def review_task(task_id: int, payload: ReviewAction, request: Request,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.query(GenerationTask).filter(GenerationTask.id == task_id).first()
    if not t:
        raise HTTPException(404, "task not found")

    action = payload.action.lower()
    if action not in _ACTION_PERMS:
        raise HTTPException(400, f"unknown action {action}")
    audit_action, required_perm = _ACTION_PERMS[action]

    require_permission(db, user, t.project_id, required_perm)

    before = snapshot(t, _TASK_FIELDS)
    cascaded_task_id = None

    if action == "approve":
        t.status = TaskStatus.approved
        # Cascading next pipeline step. This is a *system-initiated* task
        # creation triggered by an approved review — it intentionally does NOT
        # require task.create permission on the reviewer (otherwise reviewers
        # could never advance the pipeline). The cascade is recorded in the
        # audit log via the `after_value.cascaded_task` field.
        if t.task_type == TaskType.script_generation and t.episode_id:
            new_task = GenerationTask(
                project_id=t.project_id, episode_id=t.episode_id,
                task_type=TaskType.storyboard_generation,
                status=TaskStatus.pending,
                owner_id=t.owner_id, created_by=user.id,
                input_data={"cascaded_from_task": t.id},
            )
            db.add(new_task); db.commit(); db.refresh(new_task)
            cascaded_task_id = new_task.id
            run_generation_task.delay(new_task.id)
        elif t.task_type == TaskType.storyboard_generation and t.episode_id:
            new_task = GenerationTask(
                project_id=t.project_id, episode_id=t.episode_id,
                task_type=TaskType.video_generation,
                status=TaskStatus.pending,
                owner_id=t.owner_id, created_by=user.id,
                input_data={"cascaded_from_task": t.id},
            )
            db.add(new_task); db.commit(); db.refresh(new_task)
            cascaded_task_id = new_task.id
            run_generation_task.delay(new_task.id)

    elif action in ("rewrite", "enhance_conflict", "enhance_cliffhanger", "enhance_hook"):
        if t.task_type != TaskType.script_generation or not t.episode_id:
            raise HTTPException(400, "this action only applies to script tasks")
        cur = db.query(Script).filter(
            Script.episode_id == t.episode_id, Script.is_current == True
        ).first()
        if not cur:
            raise HTTPException(400, "no current script to modify")
        if action == "rewrite":
            new_content = ai_service.rewrite_script(cur.content, payload.note or "整体重写")
        elif action == "enhance_conflict":
            new_content = ai_service.enhance_conflict(cur.content)
        elif action == "enhance_cliffhanger":
            new_content = ai_service.enhance_cliffhanger(cur.content)
        else:
            new_content = ai_service.enhance_hook(cur.content)
        db.query(Script).filter(Script.episode_id == t.episode_id).update({"is_current": False})
        next_v = cur.version + 1
        new_s = Script(
            episode_id=cur.episode_id, version=next_v, content=new_content,
            title=cur.title, tags=cur.tags, cover_text=cur.cover_text,
            is_current=True, created_by=user.id,
        )
        db.add(new_s)
        t.status = TaskStatus.waiting_human
        t.output_data = {**(t.output_data or {}), "last_action": action, "new_script_version": next_v}

    elif action == "redo":
        t.status = TaskStatus.pending
        t.error = None
        db.commit()
        run_generation_task.delay(t.id)

    elif action == "reject":
        t.status = TaskStatus.rejected
        t.error = payload.note or "rejected by reviewer"

    db.commit()
    db.refresh(t)

    after = snapshot(t, _TASK_FIELDS)
    if cascaded_task_id is not None:
        after = {**after, "cascaded_task_id": cascaded_task_id}
    log_audit(
        db, user, audit_action,
        project_id=t.project_id, task_id=t.id,
        target_type="task", target_id=t.id,
        before=before, after={**after, "note": payload.note},
        request=request,
    )
    return t
