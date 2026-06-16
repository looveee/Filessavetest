"""Provider registry / factory.

Resolves the active provider from settings and constructs it with the right
config. Provider-specific env vars win over the generic AI_* fallbacks.

Nothing here ever returns an API key to a caller — only booleans about
whether config is present.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.config import settings as _global_settings

from .base import BaseProvider
from .claude_provider import ClaudeProvider
from .mock_provider import MockProvider
from .openai_compatible_provider import OpenAICompatibleProvider
from .openai_provider import OpenAIProvider

# canonical name -> class. "anthropic" is accepted as an alias for "claude".
_PROVIDERS = {
    "mock": MockProvider,
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "openai_compatible": OpenAICompatibleProvider,
}
_ALIASES = {"anthropic": "claude"}

KNOWN_PROVIDERS = tuple(_PROVIDERS.keys())


def normalize_name(name: str) -> str:
    n = (name or "mock").strip().lower()
    return _ALIASES.get(n, n)


def _common_kwargs(s) -> Dict[str, Any]:
    return {
        "timeout": s.AI_TIMEOUT_SECONDS,
        "max_retries": s.AI_MAX_RETRIES,
        "temperature": s.AI_TEMPERATURE,
        "max_output_tokens": s.AI_MAX_OUTPUT_TOKENS,
    }


def _resolve_config(name: str, s) -> Dict[str, Any]:
    """Resolve (model, api_key, base_url) for a provider from settings."""
    if name == "claude":
        return {
            "model": s.CLAUDE_MODEL or s.AI_MODEL,
            "api_key": s.CLAUDE_API_KEY or s.AI_API_KEY,
            "base_url": s.AI_BASE_URL,
        }
    if name == "openai":
        return {
            "model": s.OPENAI_MODEL or s.AI_MODEL,
            "api_key": s.OPENAI_API_KEY or s.AI_API_KEY,
            "base_url": s.OPENAI_BASE_URL or s.AI_BASE_URL,
        }
    if name == "openai_compatible":
        return {
            "model": s.LOCAL_LLM_MODEL or s.AI_MODEL,
            "api_key": s.OPENAI_API_KEY or s.AI_API_KEY,
            "base_url": s.LOCAL_LLM_BASE_URL or s.AI_BASE_URL,
        }
    # mock
    return {"model": s.AI_MODEL, "api_key": "", "base_url": ""}


def build_provider(name: str, s=None) -> BaseProvider:
    s = s or _global_settings
    canonical = normalize_name(name)
    cls = _PROVIDERS.get(canonical, MockProvider)
    cfg = _resolve_config(canonical, s)
    return cls(**cfg, **_common_kwargs(s))


def get_provider(s=None) -> BaseProvider:
    """The provider selected by settings.AI_PROVIDER."""
    s = s or _global_settings
    return build_provider(s.AI_PROVIDER, s)


def provider_status(s=None) -> Dict[str, Any]:
    """Safe summary for GET /api/ai/providers — no secrets."""
    s = s or _global_settings
    current = normalize_name(s.AI_PROVIDER)
    providers: List[Dict[str, Any]] = []
    for name in KNOWN_PROVIDERS:
        p = build_provider(name, s)
        missing = p.missing_config()
        providers.append({
            "name": name,
            "model": p.model,
            "configured": not missing,
            "missing_config": missing,
        })
    cur = build_provider(current, s)
    return {
        "current_provider": current,
        "current_model": cur.model,
        "configured": not cur.missing_config(),
        "missing_config": cur.missing_config(),
        "providers": providers,
    }


def validate_current_provider(s=None) -> List[str]:
    """Return missing-config field names for the active provider (for startup)."""
    s = s or _global_settings
    return get_provider(s).missing_config()
