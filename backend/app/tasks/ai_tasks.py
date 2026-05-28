"""
Celery tasks. Each AI step is wrapped here so it can be queued and tracked.

v0.6: real AI providers via app.services.ai. Each step:
  - calls the matching ai_service method (which logs an ai_generation_runs row)
  - on AI failure (provider error / unparseable / schema-invalid) marks the
    task `failed` and writes a task.ai_failed audit row
  - on success writes STRUCTURED JSON to output_data and a task.ai_completed
    audit row

video_generation is intentionally still a mock placeholder — v0.6 is the
text link only (no real video model).
"""
import hashlib
import os
from datetime import datetime

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models import (
    GenerationTask, TaskStatus, TaskType,
    Project, Episode, Script, Storyboard, Asset,
)
from app.services.ai import ai_service
from app.services.audit import log_audit


def _set_status(db, task: GenerationTask, status: TaskStatus, **fields):
    task.status = status
    for k, v in fields.items():
        setattr(task, k, v)
    task.updated_at = datetime.utcnow()
    db.commit()


def _run_ctx(task: GenerationTask) -> dict:
    return {
        "project_id": task.project_id,
        "task_id": task.id,
        "episode_id": task.episode_id,
        "created_by": task.created_by,
    }


def _ai_meta(resp) -> dict:
    return {
        "provider": resp.provider,
        "model": resp.model,
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
        "latency_ms": resp.latency_ms,
        "prompt_template_key": resp.prompt_template_key,
        "prompt_template_version": resp.prompt_template_version,
    }


def _audit_completed(db, task: GenerationTask, resp):
    log_audit(db, None, "task.ai_completed",
              project_id=task.project_id, task_id=task.id,
              target_type="task", target_id=task.id,
              after=_ai_meta(resp))


def _audit_failed(db, task: GenerationTask, resp_or_err):
    after = _ai_meta(resp_or_err) if hasattr(resp_or_err, "provider") else {}
    after = {**after, "error": getattr(resp_or_err, "error", str(resp_or_err))}
    log_audit(db, None, "task.ai_failed",
              project_id=task.project_id, task_id=task.id,
              target_type="task", target_id=task.id,
              after=after)


def _fail(db, task: GenerationTask, resp_or_err, msg: str):
    _set_status(db, task, TaskStatus.failed, error=msg)
    _audit_failed(db, task, resp_or_err)


@celery_app.task(bind=True, name="app.tasks.ai_tasks.run_generation_task")
def run_generation_task(self, task_id: int):
    """Dispatch a GenerationTask by its task_type and run the corresponding AI op."""
    db = SessionLocal()
    try:
        task = db.query(GenerationTask).filter(GenerationTask.id == task_id).first()
        if not task:
            return {"ok": False, "error": "task not found"}

        _set_status(db, task, TaskStatus.running, celery_task_id=self.request.id)

        try:
            if task.task_type == TaskType.outline_generation:
                _do_outline(db, task)
            elif task.task_type == TaskType.episode_split:
                _do_episode_split(db, task)
            elif task.task_type == TaskType.script_generation:
                _do_script(db, task)
            elif task.task_type == TaskType.storyboard_generation:
                _do_storyboard(db, task)
            elif task.task_type == TaskType.review:
                _do_review(db, task)
            elif task.task_type == TaskType.video_generation:
                _do_video(db, task)
            else:
                _set_status(db, task, TaskStatus.failed, error=f"unsupported type {task.task_type}")
            return {"ok": True, "task_id": task_id}

        except Exception as e:  # noqa: BLE001
            _fail(db, task, e, f"task crashed: {e}")
            return {"ok": False, "error": str(e)}

    finally:
        db.close()


# ---------------------------------------------------------------------
# Individual step handlers
# ---------------------------------------------------------------------
def _do_outline(db, task):
    project = db.query(Project).filter(Project.id == task.project_id).first()
    resp = ai_service.generate_outline(
        db, run_ctx=_run_ctx(task),
        theme=task.input_data.get("theme", project.name if project else ""),
        genre=project.genre if project else "",
        style=project.style if project else "",
        target_episodes=project.target_episodes if project else 10,
    )
    if not resp.ok:
        _fail(db, task, resp, f"outline AI failed: {resp.error}")
        return
    _set_status(db, task, TaskStatus.completed, output_data=resp.parsed_json,
                ai_score=None)
    _audit_completed(db, task, resp)


def _do_episode_split(db, task):
    outline = task.input_data.get("outline") or {"theme": ""}
    total = task.input_data.get("total", 10)
    resp = ai_service.split_episodes(db, run_ctx=_run_ctx(task), outline=outline, total=total)
    if not resp.ok:
        _fail(db, task, resp, f"episode split AI failed: {resp.error}")
        return
    episodes = resp.parsed_json.get("episodes", [])
    for ep in episodes:
        db.add(Episode(
            project_id=task.project_id,
            episode_number=ep["episode_number"],
            title=ep["title"],
            summary=ep.get("summary", ""),
            status="draft",
        ))
    db.commit()
    _set_status(db, task, TaskStatus.completed,
                output_data={"count": len(episodes), "episodes": episodes})
    _audit_completed(db, task, resp)


def _do_script(db, task):
    ep = db.query(Episode).filter(Episode.id == task.episode_id).first()
    if not ep:
        _fail(db, task, RuntimeError("episode missing"), "episode missing")
        return
    project = db.query(Project).filter(Project.id == task.project_id).first()
    resp = ai_service.generate_script(
        db, run_ctx=_run_ctx(task),
        episode_number=ep.episode_number,
        episode_title=ep.title or f"第{ep.episode_number}集",
        episode_summary=ep.summary or "",
        genre=project.genre if project else "",
        style=project.style if project else "",
    )
    if not resp.ok:
        _fail(db, task, resp, f"script AI failed: {resp.error}")
        return

    result = resp.parsed_json
    db.query(Script).filter(Script.episode_id == ep.id).update({"is_current": False})
    last = db.query(Script).filter(Script.episode_id == ep.id).order_by(Script.version.desc()).first()
    next_version = (last.version + 1) if last else 1
    s = Script(
        episode_id=ep.id,
        version=next_version,
        content=result["script"],
        title=result.get("episode_title") or ep.title,
        tags=result.get("tags", []),
        cover_text=result.get("cover_text", ""),
        is_current=True,
        created_by=task.created_by,
    )
    db.add(s)
    db.flush()

    quality = result.get("quality_score")
    risk = result.get("risk_score") or 0
    risk_flags = ["high_risk"] if risk >= 60 else []
    ep.ai_score = quality
    ep.risk_flags = risk_flags
    db.commit()

    _set_status(
        db, task, TaskStatus.waiting_human,
        output_data={**result, "script_id": s.id},
        ai_score=quality,
        risk_flags=risk_flags,
    )
    _audit_completed(db, task, resp)


def _do_storyboard(db, task):
    ep = db.query(Episode).filter(Episode.id == task.episode_id).first()
    script = db.query(Script).filter(
        Script.episode_id == task.episode_id, Script.is_current == True  # noqa: E712
    ).first()
    if not ep or not script:
        _fail(db, task, RuntimeError("episode/script missing"), "episode/script missing")
        return
    resp = ai_service.generate_storyboard(
        db, run_ctx=_run_ctx(task),
        script_text=script.content or "",
        episode_title=ep.title or "",
        shot_count=task.input_data.get("shots", 8),
    )
    if not resp.ok:
        _fail(db, task, resp, f"storyboard AI failed: {resp.error}")
        return

    shots = resp.parsed_json.get("shots", [])
    db.query(Storyboard).filter(Storyboard.episode_id == ep.id).delete()
    for sh in shots:
        db.add(Storyboard(
            episode_id=ep.id,
            shot_number=sh.get("shot_no"),
            duration_sec=sh.get("duration_sec", 3.0),
            visual=sh.get("visual", ""),
            action=sh.get("character_action", ""),
            voiceover=sh.get("narration", ""),
            subtitle=sh.get("subtitle", ""),
            sfx=sh.get("sound_effect", ""),
            bgm=sh.get("bgm_suggestion", ""),
        ))
    db.commit()
    _set_status(db, task, TaskStatus.waiting_human,
                output_data={"shot_count": len(shots), "shots": shots})
    _audit_completed(db, task, resp)


def _do_review(db, task):
    script = db.query(Script).filter(
        Script.episode_id == task.episode_id, Script.is_current == True  # noqa: E712
    ).first()
    if not script:
        _fail(db, task, RuntimeError("no current script"), "no current script to review")
        return
    resp = ai_service.review_content(db, run_ctx=_run_ctx(task), content=script.content or "")
    if not resp.ok:
        _fail(db, task, resp, f"review AI failed: {resp.error}")
        return
    result = resp.parsed_json
    risk_flags = result.get("risk_flags", [])
    _set_status(db, task, TaskStatus.waiting_human,
                output_data=result,
                ai_score=max(0.0, 100.0 - float(result.get("risk_score") or 0)),
                risk_flags=risk_flags)
    _audit_completed(db, task, resp)


def _do_video(db, task):
    """Mock video placeholder — no AI provider call in v0.6."""
    ep = db.query(Episode).filter(Episode.id == task.episode_id).first()
    shots = db.query(Storyboard).filter(Storyboard.episode_id == (ep.id if ep else None)).all()
    fake_id = hashlib.md5(f"{task.id}-{task.episode_id}".encode()).hexdigest()[:10]
    duration = sum((s.duration_sec or 3.0) for s in shots) or 30.0
    # Write placeholders UNDER ASSETS_DIR so the auth-checked download
    # endpoint's path-containment check passes.
    os.makedirs(settings.ASSETS_DIR, exist_ok=True)
    video_path = os.path.join(settings.ASSETS_DIR, f"mock_video_{fake_id}.mp4")
    cover_path = os.path.join(settings.ASSETS_DIR, f"mock_cover_{fake_id}.jpg")
    try:
        with open(video_path, "wb") as f:
            f.write(b"MOCK_VIDEO_PLACEHOLDER")
        with open(cover_path, "wb") as f:
            f.write(b"MOCK_COVER_PLACEHOLDER")
    except OSError:
        pass
    a = Asset(
        project_id=task.project_id,
        episode_id=task.episode_id,
        asset_type="video",
        file_path=video_path,
        cover_path=cover_path,
        duration_sec=duration,
        status="ready",
        meta={"mock": True},
    )
    db.add(a)
    db.commit()
    _set_status(db, task, TaskStatus.in_review, output_data={"asset_id": a.id})
