"""All database models. Single module for clarity."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey,
    JSON, Float, UniqueConstraint, Enum as SAEnum, Index
)
from sqlalchemy.orm import relationship
import enum
from app.database import Base


# ------------------ Enums ------------------

class TaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    waiting_human = "waiting_human"
    assigned = "assigned"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"
    revision_required = "revision_required"
    failed = "failed"
    completed = "completed"


class TaskType(str, enum.Enum):
    outline_generation = "outline_generation"
    episode_split = "episode_split"
    script_generation = "script_generation"
    storyboard_generation = "storyboard_generation"
    video_generation = "video_generation"
    editing = "editing"
    review = "review"
    publish = "publish"


class ProjectRole(str, enum.Enum):
    owner = "owner"
    manager = "manager"
    editor = "editor"
    storyboarder = "storyboarder"
    reviewer = "reviewer"
    publisher = "publisher"
    viewer = "viewer"


class PublishStatus(str, enum.Enum):
    pending = "pending"        # 待发布
    published = "published"    # 已发布
    failed = "failed"          # 失败
    skipped = "skipped"        # 跳过


# ------------------ Users / Roles ------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(64), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(128))
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    projects = relationship("Project", back_populates="owner", foreign_keys="Project.owner_id")
    memberships = relationship("ProjectMember", back_populates="user", cascade="all, delete-orphan")


# ------------------ Projects ------------------

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    genre = Column(String(64))           # 题材
    style = Column(String(64))           # 风格
    target_episodes = Column(Integer, default=0)
    status = Column(String(32), default="draft")  # draft / active / archived
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="projects", foreign_keys=[owner_id])
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    source_texts = relationship("SourceText", back_populates="project", cascade="all, delete-orphan")
    episodes = relationship("Episode", back_populates="project", cascade="all, delete-orphan")
    tasks = relationship("GenerationTask", back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_user"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(SAEnum(ProjectRole), default=ProjectRole.viewer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="members")
    user = relationship("User", back_populates="memberships")


# ------------------ Source Text (uploaded novel) ------------------

class SourceText(Base):
    __tablename__ = "source_texts"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255))
    content = Column(Text)               # Raw text
    char_count = Column(Integer, default=0)
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="source_texts")


# ------------------ Episodes / Scripts ------------------

class Episode(Base):
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_number = Column(Integer, nullable=False)
    title = Column(String(255))
    summary = Column(Text)
    status = Column(String(32), default="draft")
    ai_score = Column(Float)             # AI 自评分 0-100
    risk_flags = Column(JSON, default=list)  # ["敏感词", "低能量开场"] etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="episodes")
    scripts = relationship("Script", back_populates="episode", cascade="all, delete-orphan")
    storyboards = relationship("Storyboard", back_populates="episode", cascade="all, delete-orphan")
    assets = relationship("Asset", back_populates="episode")


class Script(Base):
    __tablename__ = "scripts"

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, default=1)
    content = Column(Text)               # Full script text
    title = Column(String(255))
    tags = Column(JSON, default=list)
    cover_text = Column(String(255))
    is_current = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    episode = relationship("Episode", back_populates="scripts")


class Storyboard(Base):
    __tablename__ = "storyboards"

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    shot_number = Column(Integer, nullable=False)         # 镜头序号
    duration_sec = Column(Float, default=3.0)             # 时长
    visual = Column(Text)                                 # 画面描述
    action = Column(Text)                                 # 人物动作
    voiceover = Column(Text)                              # 旁白
    subtitle = Column(Text)                               # 字幕
    sfx = Column(String(255))                             # 音效
    bgm = Column(String(255))                             # 背景音乐
    created_at = Column(DateTime, default=datetime.utcnow)

    episode = relationship("Episode", back_populates="storyboards")


# ------------------ Tasks ------------------

class GenerationTask(Base):
    __tablename__ = "generation_tasks"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_id = Column(Integer, ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True, index=True)
    task_type = Column(SAEnum(TaskType), nullable=False, index=True)
    status = Column(SAEnum(TaskStatus), default=TaskStatus.pending, nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    input_data = Column(JSON, default=dict)
    output_data = Column(JSON, default=dict)
    error = Column(Text)
    ai_score = Column(Float)
    risk_flags = Column(JSON, default=list)
    celery_task_id = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="tasks")
    assignments = relationship("TaskAssignment", back_populates="task", cascade="all, delete-orphan")


class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("generation_tasks.id", ondelete="CASCADE"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("GenerationTask", back_populates="assignments")


# ------------------ Assets (final videos) ------------------

class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_id = Column(Integer, ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True, index=True)
    asset_type = Column(String(32), default="video")  # video / cover / audio
    file_path = Column(String(512))
    cover_path = Column(String(512))
    duration_sec = Column(Float)
    status = Column(String(32), default="ready")      # ready / processing / failed
    meta = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    episode = relationship("Episode", back_populates="assets")


# ------------------ Accounts (publish accounts) ------------------

class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    platform = Column(String(64))                     # douyin / wechat / kuaishou / xhs ...
    name = Column(String(128))                        # account display name
    persona = Column(Text)                            # 人设
    domain = Column(String(64))                       # 领域
    publish_frequency = Column(String(64))            # 发布频率, e.g. "daily 18:00"
    credentials = Column(JSON, default=dict)          # tokens etc. (placeholder)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ------------------ Publish schedule ------------------

class PublishSchedule(Base):
    __tablename__ = "publish_schedules"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    status = Column(SAEnum(PublishStatus), default=PublishStatus.pending, nullable=False, index=True)
    title = Column(String(255))
    description = Column(Text)
    tags = Column(JSON, default=list)
    published_at = Column(DateTime)
    error = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)


# ------------------ Performance metrics (data feedback) ------------------

class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"

    id = Column(Integer, primary_key=True)
    schedule_id = Column(Integer, ForeignKey("publish_schedules.id", ondelete="CASCADE"), index=True)
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    completion_rate = Column(Float, default=0.0)
    collected_at = Column(DateTime, default=datetime.utcnow)


# ------------------ Audit log ------------------

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    # user_id keeps the FK so we get cascade behavior on user delete; if you
    # want audit logs to also outlive deleted users, drop ondelete here too.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                     index=True, nullable=True)
    # project_id and task_id are intentionally PLAIN INTEGER (not FKs).
    # Audit logs must outlive their referenced rows so a project/task delete
    # is never blocked by audit history. We still index them for query speed.
    project_id = Column(Integer, index=True, nullable=True)
    task_id = Column(Integer, index=True, nullable=True)
    action = Column(String(64), index=True, nullable=False)
    target_type = Column(String(32), nullable=True)
    target_id = Column(Integer, nullable=True)
    before_value = Column(JSON, nullable=True)
    after_value = Column(JSON, nullable=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ------------------ AI Prompt templates (v0.6) ------------------

class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_prompt_key_version"),)

    id = Column(Integer, primary_key=True)
    key = Column(String(64), index=True, nullable=False)        # e.g. "generate_outline"
    name = Column(String(128))
    version = Column(Integer, default=1, nullable=False)
    system_prompt = Column(Text)
    user_prompt_template = Column(Text)
    output_schema = Column(JSON, default=dict)                   # field shape hint
    is_active = Column(Boolean, default=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ------------------ AI generation run log (v0.6) ------------------

class AIGenerationRun(Base):
    """One row per AI provider call. NEVER stores prompts in clear or any
    API key — only sha256 hashes of request/response and usage metadata.

    project_id / task_id / episode_id are PLAIN INTEGER (not FKs), like
    audit_logs: this telemetry must outlive the rows it references."""
    __tablename__ = "ai_generation_runs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, index=True, nullable=True)
    task_id = Column(Integer, index=True, nullable=True)
    episode_id = Column(Integer, index=True, nullable=True)
    provider = Column(String(32), nullable=False)
    model = Column(String(128))
    prompt_template_key = Column(String(64), index=True)
    prompt_template_version = Column(Integer)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    status = Column(String(32), index=True, nullable=False)     # completed / failed
    error_message = Column(Text)
    request_hash = Column(String(64))
    response_hash = Column(String(64))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ------------------ Notifications ------------------

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title = Column(String(255))
    body = Column(Text)
    link = Column(String(512))
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
