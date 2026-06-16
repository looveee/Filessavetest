"""Structured result schemas for every AI text operation, plus the
provider-agnostic AIResponse envelope.

Every AI method MUST return JSON that validates against one of these
Pydantic models. The provider layer returns raw text; the service layer
parses + validates against the matching schema and rejects anything that
does not fit (see app.services.ai.AIService).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, BeforeValidator, Field


def _none_to_empty(v):
    """Treat JSON null as empty string for str fields — defensive against
    real LLMs that emit `null` and against nullable DB columns flowing into
    the mock builders."""
    return "" if v is None else v


# A string that coerces JSON null -> "". Use everywhere a model expects text.
LaxStr = Annotated[str, BeforeValidator(_none_to_empty)]


def _none_to_zero(v):
    return 0 if v is None else v


# Numbers that coerce JSON null -> 0.
LaxFloat = Annotated[float, BeforeValidator(_none_to_zero)]
LaxInt = Annotated[int, BeforeValidator(_none_to_zero)]


# ---------------------------------------------------------------------
# Provider response envelope (not persisted wholesale — see _record_run)
# ---------------------------------------------------------------------
@dataclass
class AIResponse:
    content: str = ""
    parsed_json: Optional[Dict[str, Any]] = None
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    # raw_response is for in-process debugging only. It is DELIBERATELY never
    # written to the database (could contain prompt text / sensitive context).
    raw_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    prompt_template_key: Optional[str] = None
    prompt_template_version: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.parsed_json is not None


# ---------------------------------------------------------------------
# 1. Outline
# ---------------------------------------------------------------------
class ActBeat(BaseModel):
    act: LaxInt
    title: LaxStr
    summary: LaxStr = ""


class OutlineResult(BaseModel):
    theme: LaxStr
    genre: LaxStr = ""
    style: LaxStr = ""
    logline: LaxStr
    act_structure: List[ActBeat] = Field(default_factory=list)
    target_episodes: LaxInt = 0


# ---------------------------------------------------------------------
# 2. Episode split
# ---------------------------------------------------------------------
class EpisodeItem(BaseModel):
    episode_number: LaxInt
    title: LaxStr
    summary: LaxStr = ""


class EpisodeSplitResult(BaseModel):
    episodes: List[EpisodeItem] = Field(default_factory=list)


# ---------------------------------------------------------------------
# 3. Script
# ---------------------------------------------------------------------
class DialogueLine(BaseModel):
    character: LaxStr = ""
    line: LaxStr = ""


class ScriptResult(BaseModel):
    episode_title: LaxStr
    hook: LaxStr
    script: LaxStr
    dialogues: List[DialogueLine] = Field(default_factory=list)
    cliffhanger: LaxStr = ""
    cover_text: LaxStr = ""
    tags: List[str] = Field(default_factory=list)
    risk_score: LaxFloat = 0.0
    quality_score: LaxFloat = 0.0
    notes: LaxStr = ""


# ---------------------------------------------------------------------
# 4. Storyboard
# ---------------------------------------------------------------------
class Shot(BaseModel):
    shot_no: LaxInt
    duration_sec: LaxFloat = 3.0
    visual: LaxStr = ""
    character_action: LaxStr = ""
    narration: LaxStr = ""
    subtitle: LaxStr = ""
    sound_effect: LaxStr = ""
    bgm_suggestion: LaxStr = ""


class StoryboardResult(BaseModel):
    shots: List[Shot] = Field(default_factory=list)


# ---------------------------------------------------------------------
# 5. Title / tags
# ---------------------------------------------------------------------
class TitleTagsResult(BaseModel):
    title: LaxStr
    tags: List[str] = Field(default_factory=list)
    cover_text: LaxStr = ""


# ---------------------------------------------------------------------
# 6. Content review (safety + originality)
# ---------------------------------------------------------------------
class ContentReviewResult(BaseModel):
    risk_score: LaxFloat = 0.0
    risk_flags: List[str] = Field(default_factory=list)
    originality_notes: LaxStr = ""
    rewrite_suggestion: LaxStr = ""


# ---------------------------------------------------------------------
# 7. Rewrite
# ---------------------------------------------------------------------
class RewriteResult(BaseModel):
    script: LaxStr
    notes: LaxStr = ""


# ---------------------------------------------------------------------
# 8. Enhancement (hook / conflict / cliffhanger)
# ---------------------------------------------------------------------
class EnhancementResult(BaseModel):
    script: LaxStr
    notes: LaxStr = ""


# op key -> result schema. The single source of truth for which model an
# operation must validate against.
OP_SCHEMAS: Dict[str, type[BaseModel]] = {
    "generate_outline": OutlineResult,
    "split_episodes": EpisodeSplitResult,
    "generate_script": ScriptResult,
    "generate_storyboard": StoryboardResult,
    "generate_title_tags": TitleTagsResult,
    "review_content": ContentReviewResult,
    "rewrite_script": RewriteResult,
    "enhance_hook": EnhancementResult,
    "enhance_conflict": EnhancementResult,
    "enhance_cliffhanger": EnhancementResult,
}
