"""Pydantic schemas for API request/response shapes."""
from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ============ User ============

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    username: str        # accept username OR email
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    username: str
    full_name: Optional[str] = None
    is_active: bool
    is_admin: bool
    created_at: datetime


class UserPublic(BaseModel):
    """Minimal public profile — used by /users/lookup. No email, no flags."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: Optional[str] = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ============ Project ============

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    genre: Optional[str] = None
    style: Optional[str] = None
    target_episodes: int = 0


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    style: Optional[str] = None
    target_episodes: Optional[int] = None
    status: Optional[str] = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: Optional[str] = None
    genre: Optional[str] = None
    style: Optional[str] = None
    target_episodes: int
    status: str
    owner_id: int
    created_at: datetime
    updated_at: datetime


class MemberAdd(BaseModel):
    user_id: int
    role: str       # ProjectRole value


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    user_id: int
    role: str
    created_at: datetime


# ============ Source Text ============

class SourceTextOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    filename: Optional[str]
    char_count: int
    created_at: datetime


# ============ Episode / Script / Storyboard ============

class EpisodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    episode_number: int
    title: Optional[str]
    summary: Optional[str]
    status: str
    ai_score: Optional[float]
    risk_flags: List[str] = []
    created_at: datetime


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    episode_id: int
    version: int
    content: Optional[str]
    title: Optional[str]
    tags: List[str] = []
    cover_text: Optional[str]
    is_current: bool
    created_at: datetime


class StoryboardCreate(BaseModel):
    shot_number: int
    duration_sec: float = 3.0
    visual: Optional[str] = None
    action: Optional[str] = None
    voiceover: Optional[str] = None
    subtitle: Optional[str] = None
    sfx: Optional[str] = None
    bgm: Optional[str] = None


class StoryboardOut(StoryboardCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    episode_id: int


# ============ Tasks ============

class TaskCreate(BaseModel):
    project_id: int
    task_type: str
    episode_id: Optional[int] = None
    assigned_to: Optional[int] = None
    input_data: Dict[str, Any] = {}


class TaskUpdate(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[int] = None
    output_data: Optional[Dict[str, Any]] = None
    ai_score: Optional[float] = None
    risk_flags: Optional[List[str]] = None
    error: Optional[str] = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    episode_id: Optional[int]
    task_type: str
    status: str
    owner_id: int
    created_by: int
    assigned_to: Optional[int]
    input_data: Dict[str, Any] = {}
    output_data: Dict[str, Any] = {}
    ai_score: Optional[float]
    risk_flags: List[str] = []
    error: Optional[str]
    created_at: datetime
    updated_at: datetime


# ============ Review action ============

class ReviewAction(BaseModel):
    action: str   # approve | rewrite | enhance_conflict | enhance_cliffhanger | redo | reject
    note: Optional[str] = None


# ============ Asset ============

class AssetOut(BaseModel):
    """Public asset shape — never exposes internal disk paths.
    Use AssetDebugOut (admin only) for debugging."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    episode_id: Optional[int] = None
    asset_type: str
    duration_sec: Optional[float] = None
    status: str
    created_at: datetime
    download_url: Optional[str] = None  # populated by the API layer


class AssetDebugOut(BaseModel):
    """Admin-only diagnostic view that includes internal paths and metadata.
    Returned by GET /api/assets/{id}/debug."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    episode_id: Optional[int] = None
    asset_type: str
    file_path: Optional[str] = None
    cover_path: Optional[str] = None
    duration_sec: Optional[float] = None
    status: str
    meta: Any = None
    created_at: datetime


# ============ Account / Schedule ============

class AccountCreate(BaseModel):
    platform: str
    name: str
    persona: Optional[str] = None
    domain: Optional[str] = None
    publish_frequency: Optional[str] = None


class AccountOut(AccountCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_id: int
    is_active: bool
    created_at: datetime


class ScheduleCreate(BaseModel):
    asset_id: int
    account_id: int
    scheduled_at: datetime
    title: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = []


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: Optional[int]
    asset_id: int
    account_id: int
    scheduled_at: datetime
    status: str
    title: Optional[str]
    description: Optional[str]
    tags: List[str] = []
    published_at: Optional[datetime]
    created_at: datetime


# ============ AI generic ============

class AIGenerateRequest(BaseModel):
    project_id: int
    episode_id: Optional[int] = None
    extra: Dict[str, Any] = {}


# ============ Audit log ============

class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: Optional[int] = None
    project_id: Optional[int] = None
    task_id: Optional[int] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    before_value: Optional[Any] = None
    after_value: Optional[Any] = None
    ip: Optional[str] = None
    created_at: datetime
