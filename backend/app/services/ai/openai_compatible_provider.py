"""OpenAI-compatible provider for local servers (Ollama / vLLM / LM Studio).

Same wire format as OpenAI Chat Completions, but:
  - base_url points at the local server (e.g. http://host.docker.internal:11434/v1)
  - api_key is optional (Ollama ignores it; vLLM may require any non-empty token)
  - we do NOT send response_format json_object — many local servers reject it.
    Instead we lean on the prompt to enforce JSON.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseProvider, ProviderConfigError
from .schemas import AIResponse

_JSON_NUDGE = "\n\nRespond with valid JSON only. No markdown, no commentary."


class OpenAICompatibleProvider(BaseProvider):
    name = "openai_compatible"

    def missing_config(self) -> List[str]:
        missing = []
        if not self.base_url:
            missing.append("LOCAL_LLM_BASE_URL")
        if not self.model:
            missing.append("LOCAL_LLM_MODEL")
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
            raise ProviderConfigError(f"openai_compatible provider missing config: {missing}")

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "messages": [
                {"role": "system", "content": (system or "") + _JSON_NUDGE},
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
