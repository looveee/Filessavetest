"""OpenAI Chat Completions provider."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseProvider, JSON_ONLY_NUDGE, ProviderConfigError
from .schemas import AIResponse

_DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAIProvider(BaseProvider):
    name = "openai"

    def missing_config(self) -> List[str]:
        missing = []
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if not self.model:
            missing.append("OPENAI_MODEL")
        return missing

    def generate_json(
        self,
        system: str,
        user: str,
        *,
        schema: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> AIResponse:
        missing = self.missing_config()
        if missing:
            raise ProviderConfigError(f"openai provider missing config: {missing}")

        base = self.base_url or _DEFAULT_BASE
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": (system or "") + JSON_ONLY_NUDGE},
                {"role": "user", "content": user or ""},
            ],
        }
        body, latency_ms = self._http_post(url, headers, payload)

        choices = body.get("choices") or []
        content = ""
        if choices:
            content = (choices[0].get("message") or {}).get("content", "") or ""
        in_tok, out_tok = self.normalize_usage(body.get("usage"))
        return AIResponse(
            content=content,
            provider=self.name,
            model=body.get("model") or self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
        )
