"""AI service package.

Public surface:
  - ai_service: high-level singleton with one method per text operation.
  - get_provider / provider_status / validate_current_provider (registry).
  - AIResponse and the result schemas.

The service owns the contract: load template -> render prompt -> call
provider -> parse + validate (+ one repair attempt) -> record an
ai_generation_runs row -> return an AIResponse. Providers stay dumb; all
JSON discipline lives here.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional

from pydantic import ValidationError

from .base import BaseProvider
from .default_prompts import DEFAULT_PROMPT_TEMPLATES
from .json_repair import repair_loads
from .prompt_renderer import render_prompt
from .registry import (
    get_provider,
    provider_status,
    validate_current_provider,
    KNOWN_PROVIDERS,
)
from .schemas import (
    AIResponse,
    OP_SCHEMAS,
    OutlineResult,
    EpisodeSplitResult,
    ScriptResult,
    StoryboardResult,
    TitleTagsResult,
    ContentReviewResult,
    RewriteResult,
    EnhancementResult,
)

log = logging.getLogger("ai")


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class AIService:
    """Orchestrates a single structured AI call end to end."""

    # ---- template loading ----
    def load_template(self, db, op_key: str) -> Dict[str, Any]:
        """Active DB template for `op_key`, falling back to code defaults."""
        if db is not None:
            try:
                from app.models import PromptTemplate
                row = (
                    db.query(PromptTemplate)
                    .filter(PromptTemplate.key == op_key, PromptTemplate.is_active.is_(True))
                    .order_by(PromptTemplate.version.desc())
                    .first()
                )
                if row:
                    return {
                        "key": row.key,
                        "version": row.version,
                        "system_prompt": row.system_prompt or "",
                        "user_prompt_template": row.user_prompt_template or "",
                        "output_schema": row.output_schema,
                    }
            except Exception as e:  # noqa: BLE001 — fall back to defaults on any DB issue
                log.warning("prompt template load failed for %s: %s", op_key, e)

        d = DEFAULT_PROMPT_TEMPLATES.get(op_key)
        if not d:
            raise KeyError(f"no prompt template for op {op_key!r}")
        return {
            "key": d["key"],
            "version": d["version"],
            "system_prompt": d["system_prompt"],
            "user_prompt_template": d["user_prompt_template"],
            "output_schema": d.get("output_schema"),
        }

    # ---- parse + validate + repair ----
    def _parse_validate(self, content: str, schema_cls):
        obj = None
        try:
            obj = json.loads(content)
        except Exception:
            obj = None
        if obj is None:
            # repair once
            try:
                obj = repair_loads(content)
            except Exception as e:  # noqa: BLE001
                return None, f"json parse failed after repair: {e}"
        try:
            model = schema_cls.model_validate(obj)
            return model.model_dump(), None
        except ValidationError as e:
            return None, f"schema validation failed: {e}"

    # ---- run logging ----
    def _record_run(self, db, resp: AIResponse, run_ctx, status: str,
                    system: str, user: str) -> None:
        if db is None:
            return
        try:
            from app.models import AIGenerationRun
            rc = run_ctx or {}
            run = AIGenerationRun(
                project_id=rc.get("project_id"),
                task_id=rc.get("task_id"),
                episode_id=rc.get("episode_id"),
                provider=resp.provider or "",
                model=resp.model or "",
                prompt_template_key=resp.prompt_template_key,
                prompt_template_version=resp.prompt_template_version,
                input_tokens=resp.input_tokens or 0,
                output_tokens=resp.output_tokens or 0,
                latency_ms=resp.latency_ms or 0,
                status=status,
                error_message=(resp.error or "")[:2000] or None,
                # Hash prompt + response so we can dedupe / audit WITHOUT ever
                # storing the prompt text or any API key.
                request_hash=_sha256((system or "") + "\n" + (user or "")),
                response_hash=_sha256(resp.content or ""),
                created_by=rc.get("created_by"),
            )
            db.add(run)
            db.commit()
        except Exception as e:  # noqa: BLE001 — logging must not break the pipeline
            log.warning("ai_generation_runs write failed: %s", e)
            try:
                db.rollback()
            except Exception:
                pass

    # ---- core ----
    def run(self, db, op_key: str, context: Dict[str, Any], *, run_ctx=None) -> AIResponse:
        provider: BaseProvider = get_provider()
        tmpl = self.load_template(db, op_key)
        system_prompt = tmpl["system_prompt"]
        user_prompt = render_prompt(tmpl["user_prompt_template"], context or {})

        call_ctx = dict(context or {})
        call_ctx["_op"] = op_key

        try:
            resp = provider.generate_json(
                system_prompt, user_prompt,
                schema=tmpl.get("output_schema"), context=call_ctx,
            )
        except Exception as e:  # noqa: BLE001
            resp = AIResponse(
                provider=getattr(provider, "name", ""),
                model=getattr(provider, "model", ""),
                error=f"provider error: {e}",
            )

        resp.prompt_template_key = tmpl["key"]
        resp.prompt_template_version = tmpl["version"]

        if resp.error is None:
            parsed, err = self._parse_validate(resp.content, OP_SCHEMAS[op_key])
            resp.parsed_json = parsed
            if parsed is None:
                resp.error = err

        self._record_run(
            db, resp, run_ctx,
            "completed" if resp.ok else "failed",
            system_prompt, user_prompt,
        )
        return resp

    # ---- freeform probe (admin /api/ai/test) ----
    def raw_test(self, db, prompt: str, *, system: str = "", run_ctx=None) -> AIResponse:
        provider: BaseProvider = get_provider()
        sys_prompt = system or "You are a helpful assistant. Reply concisely."
        try:
            resp = provider.generate_json(
                sys_prompt, prompt, context={"_op": "", "prompt": prompt},
            )
        except Exception as e:  # noqa: BLE001
            resp = AIResponse(
                provider=getattr(provider, "name", ""),
                model=getattr(provider, "model", ""),
                error=f"provider error: {e}",
            )
        resp.prompt_template_key = "ai.test"
        resp.prompt_template_version = 0
        self._record_run(
            db, resp, run_ctx,
            "completed" if resp.error is None else "failed",
            sys_prompt, prompt,
        )
        return resp

    # ---- typed wrappers ----
    def generate_outline(self, db, *, run_ctx=None, theme: str, genre: str = "",
                         style: str = "", target_episodes: int = 10) -> AIResponse:
        return self.run(db, "generate_outline", {
            "theme": theme, "genre": genre, "style": style,
            "target_episodes": target_episodes,
        }, run_ctx=run_ctx)

    def split_episodes(self, db, *, run_ctx=None, outline: Dict[str, Any],
                       total: int = 10) -> AIResponse:
        return self.run(db, "split_episodes", {
            "outline_json": outline, "total": total,
        }, run_ctx=run_ctx)

    def generate_script(self, db, *, run_ctx=None, episode_number: int,
                        episode_title: str, episode_summary: str = "",
                        genre: str = "", style: str = "") -> AIResponse:
        return self.run(db, "generate_script", {
            "episode_number": episode_number, "episode_title": episode_title,
            "episode_summary": episode_summary, "genre": genre, "style": style,
        }, run_ctx=run_ctx)

    def generate_storyboard(self, db, *, run_ctx=None, script_text: str,
                            episode_title: str = "", shot_count: int = 8) -> AIResponse:
        return self.run(db, "generate_storyboard", {
            "script_text": script_text, "episode_title": episode_title,
            "shot_count": shot_count,
        }, run_ctx=run_ctx)

    def generate_title_tags(self, db, *, run_ctx=None, content: str) -> AIResponse:
        return self.run(db, "generate_title_tags", {"content": content}, run_ctx=run_ctx)

    def review_content(self, db, *, run_ctx=None, content: str) -> AIResponse:
        return self.run(db, "review_content", {"content": content}, run_ctx=run_ctx)

    def rewrite_script(self, db, *, run_ctx=None, original_script: str,
                       instruction: str = "整体重写") -> AIResponse:
        return self.run(db, "rewrite_script", {
            "original_script": original_script, "instruction": instruction,
        }, run_ctx=run_ctx)

    def enhance_hook(self, db, *, run_ctx=None, original_script: str) -> AIResponse:
        return self.run(db, "enhance_hook", {"original_script": original_script}, run_ctx=run_ctx)

    def enhance_conflict(self, db, *, run_ctx=None, original_script: str) -> AIResponse:
        return self.run(db, "enhance_conflict", {"original_script": original_script}, run_ctx=run_ctx)

    def enhance_cliffhanger(self, db, *, run_ctx=None, original_script: str) -> AIResponse:
        return self.run(db, "enhance_cliffhanger", {"original_script": original_script}, run_ctx=run_ctx)


ai_service = AIService()

__all__ = [
    "ai_service",
    "AIService",
    "AIResponse",
    "get_provider",
    "provider_status",
    "validate_current_provider",
    "KNOWN_PROVIDERS",
    "OP_SCHEMAS",
    "OutlineResult",
    "EpisodeSplitResult",
    "ScriptResult",
    "StoryboardResult",
    "TitleTagsResult",
    "ContentReviewResult",
    "RewriteResult",
    "EnhancementResult",
]
