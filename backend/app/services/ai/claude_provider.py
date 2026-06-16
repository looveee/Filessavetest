"""Claude (Anthropic Messages API) provider."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseProvider, ProviderConfigError
from .schemas import AIResponse

_DEFAULT_BASE = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_JSON_NUDGE = "\n\n严格只输出一个合法 JSON 对象，不要使用 markdown 代码块。"


class ClaudeProvider(BaseProvider):
    name = "claude"

    def missing_config(self) -> List[str]:
        missing = []
        if not self.api_key:
            missing.append("CLAUDE_API_KEY")
        if not self.model:
            missing.append("CLAUDE_MODEL")
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
            raise ProviderConfigError(f"claude provider missing config: {missing}")

        base = self.base_url or _DEFAULT_BASE
        url = f"{base}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "system": (system or "") + _JSON_NUDGE,
            "messages": [{"role": "user", "content": user or ""}],
        }
        body, latency_ms = self._http_post(url, headers, payload)

        content = ""
        for block in body.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                content += block.get("text", "")
        in_tok, out_tok = self.normalize_usage(body.get("usage"))
        return AIResponse(
            content=content,
            provider=self.name,
            model=body.get("model") or self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
        )
