"""
Celery tasks. Each AI step is wrapped here so it can be queued and tracked.
Updates GenerationTask row + creates downstream entities (episodes, scripts, storyboards, assets).
"""
from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import (
    GenerationTask, TaskStatus, TaskType,
    Project, Episode, Script, Storyboard, Asset,
)
from app.services.ai_service import ai_service
from datetime import datetime
import time


def _set_status(db, task: GenerationTask, status: TaskStatus, **fields):
    task.status = status
    for k, v in fields.items():
        setattr(task, k, v)
    task.updated_at = datetime.utcnow()
    db.commit()


@celery_app.task(bind=True, name="app.tasks.ai_tasks.run_generation_task")
def run_generation_task(self, task_id: int):
    """Dispatch a GenerationTask by its task_type and run the corresponding AI op."""
    db = SessionLocal()
    try:
        task = db.query(GenerationTask).filter(GenerationTask.id == task_id).first()
        if not task:
            return {"ok": False, "error": "task not found"}

        _set_status(db, task, TaskStatus.running, celery_task_id=self.request.id)

        # small artificial delay to mimic real AI latency
        time.sleep(1)

        try:
            if task.task_type == TaskType.outline_generation:
                project = db.query(Project).filter(Project.id == task.project_id).first()
                outline = ai_service.generate_outline(
                    theme=task.input_data.get("theme", project.name if project else ""),
                    genre=project.genre if project else "",
                    style=project.style if project else "",
                    episodes=project.target_episodes if project else 10,
                )
                _set_status(db, task, TaskStatus.completed, output_data=outline, ai_score=85.0)

            elif task.task_type == TaskType.episode_split:
                outline = task.input_data.get("outline") or {"theme": ""}
                total = task.input_data.get("total", 10)
                episodes_data = ai_service.split_episodes(outline, total)
                # Persist Episode rows
                for ep in episodes_data:
                    e = Episode(
                        project_id=task.project_id,
                        episode_number=ep["episode_number"],
                        title=ep["title"],
                        summary=ep["summary"],
                        status="draft",
                    )
                    db.add(e)
                db.commit()
                _set_status(db, task, TaskStatus.completed, output_data={"count": len(episodes_data)})

            elif task.task_type == TaskType.script_generation:
                ep = db.query(Episode).filter(Episode.id == task.episode_id).first()
                if not ep:
                    raise RuntimeError("episode missing")
                project = db.query(Project).filter(Project.id == task.project_id).first()
                result = ai_service.generate_script(
                    {"episode_number": ep.episode_number, "title": ep.title, "summary": ep.summary},
                    {"genre": project.genre if project else "", "style": project.style if project else ""},
                )
                # Mark previous scripts not current
                db.query(Script).filter(Script.episode_id == ep.id).update({"is_current": False})
                # Find next version
                last = db.query(Script).filter(Script.episode_id == ep.id).order_by(Script.version.desc()).first()
                next_version = (last.version + 1) if last else 1
                s = Script(
                    episode_id=ep.id,
                    version=next_version,
                    content=result["content"],
                    title=result["title"],
                    tags=result["tags"],
                    cover_text=result["cover_text"],
                    is_current=True,
                    created_by=task.created_by,
                )
                db.add(s)
                ep.ai_score = result.get("ai_score")
                ep.risk_flags = result.get("risk_flags", [])
                db.commit()
                _set_status(db, task, TaskStatus.waiting_human,
                            output_data={"script_id": s.id, "title": s.title},
                            ai_score=result.get("ai_score"),
                            risk_flags=result.get("risk_flags", []))

            elif task.task_type == TaskType.storyboard_generation:
                ep = db.query(Episode).filter(Episode.id == task.episode_id).first()
                script = db.query(Script).filter(
                    Script.episode_id == task.episode_id, Script.is_current == True
                ).first()
                if not ep or not script:
                    raise RuntimeError("episode/script missing")
                shots = ai_service.generate_storyboard(script.content, shots=task.input_data.get("shots", 8))
                # Wipe existing storyboards and insert
                db.query(Storyboard).filter(Storyboard.episode_id == ep.id).delete()
                for s in shots:
                    db.add(Storyboard(episode_id=ep.id, **s))
                db.commit()
                _set_status(db, task, TaskStatus.waiting_human, output_data={"shot_count": len(shots)})

            elif task.task_type == TaskType.video_generation:
                ep = db.query(Episode).filter(Episode.id == task.episode_id).first()
                shots = db.query(Storyboard).filter(Storyboard.episode_id == ep.id).all()
                shot_dicts = [
                    {"shot_number": s.shot_number, "duration_sec": s.duration_sec,
                     "visual": s.visual, "voiceover": s.voiceover}
                    for s in shots
                ]
                video = ai_service.generate_video(shot_dicts)
                a = Asset(
                    project_id=task.project_id,
                    episode_id=ep.id,
                    asset_type="video",
                    file_path=video["file_path"],
                    cover_path=video["cover_path"],
                    duration_sec=video["duration_sec"],
                    status=video["status"],
                    meta={"mock": True},
                )
                db.add(a)
                db.commit()
                _set_status(db, task, TaskStatus.in_review, output_data={"asset_id": a.id})

            else:
                _set_status(db, task, TaskStatus.failed, error=f"unsupported type {task.task_type}")

            return {"ok": True, "task_id": task_id}

        except Exception as e:
            _set_status(db, task, TaskStatus.failed, error=str(e))
            return {"ok": False, "error": str(e)}

    finally:
        db.close()
