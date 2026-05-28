"""Provider base class + shared HTTP plumbing.

A provider's only real job is `generate_json`: take a system + user prompt,
call the model, and return an AIResponse carrying the raw text plus token
usage and latency. Parsing / validation / repair lives one level up in the
service, so providers stay thin.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .schemas import AIResponse


class ProviderConfigError(RuntimeError):
    """Raised when a provider is asked to run without required config."""


class BaseProvider:
    name: str = "base"

    def __init__(
        self,
        *,
        model: str = "",
        api_key: str = "",
        base_url: str = "",
        timeout: int = 60,
        max_retries: int = 2,
        temperature: float = 0.7,
        max_output_tokens: int = 4096,
    ) -> None:
        self.model = model or ""
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    # ---- to implement ----
    def generate_json(
        self,
        system: str,
        user: str,
        *,
        schema: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> AIResponse:
        raise NotImplementedError

    def missing_config(self) -> List[str]:
        """Return the list of required-but-empty config fields. Empty = ready."""
        return []

    # ---- shared defaults ----
    def normalize_usage(self, raw: Optional[Dict[str, Any]]) -> Tuple[int, int]:
        """Map a provider's usage object to (input_tokens, output_tokens)."""
        if not raw:
            return 0, 0
        in_tok = (
            raw.get("input_tokens")
            or raw.get("prompt_tokens")
            or 0
        )
        out_tok = (
            raw.get("output_tokens")
            or raw.get("completion_tokens")
            or 0
        )
        return int(in_tok or 0), int(out_tok or 0)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Rough USD cost estimate. 0.0 when we don't have a price for the model."""
        price = _PRICE_PER_1K.get(self.model)
        if not price:
            return 0.0
        return round(
            (input_tokens / 1000.0) * price[0] + (output_tokens / 1000.0) * price[1],
            6,
        )

    def health_check(self) -> Dict[str, Any]:
        """Config-only readiness check (no network — keep it fast & safe)."""
        missing = self.missing_config()
        return {
            "ok": not missing,
            "provider": self.name,
            "model": self.model,
            "configured": not missing,
            "missing": missing,
        }

    # ---- helpers for subclasses ----
    def _http_post(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], int]:
        """POST JSON with retry/backoff. Returns (json_body, latency_ms).

        Raises the last exception after exhausting retries.
        """
        import httpx

        attempts = max(1, self.max_retries + 1)
        last_exc: Optional[Exception] = None
        start = time.time()
        for i in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    r = client.post(url, headers=headers, json=payload)
                if r.status_code >= 500:
                    raise RuntimeError(f"upstream {r.status_code}: {r.text[:300]}")
                if r.status_code >= 400:
                    # 4xx won't get better on retry — fail fast.
                    raise RuntimeError(f"request error {r.status_code}: {r.text[:300]}")
                latency_ms = int((time.time() - start) * 1000)
                return r.json(), latency_ms
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if i < attempts - 1:
                    time.sleep(min(2 ** i, 8))
        raise last_exc if last_exc else RuntimeError("request failed")


# model -> (input $/1K, output $/1K). Best-effort, extend as needed.
_PRICE_PER_1K: Dict[str, Tuple[float, float]] = {
    "claude-opus-4-7": (0.015, 0.075),
    "claude-sonnet-4-6": (0.003, 0.015),
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
}
