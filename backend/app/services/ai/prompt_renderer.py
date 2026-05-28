"""Render a prompt template against a context dict.

`str.format_map` with a forgiving mapping: missing keys become empty
strings (so a slightly stale template never crashes a generation), and
dict/list values are JSON-encoded so they embed cleanly.
"""
from __future__ import annotations

import json
from typing import Any, Dict


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # noqa: D401
        return ""


def _coerce(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if value is None:
        return ""
    return str(value)


def render_prompt(template: str, context: Dict[str, Any]) -> str:
    safe = _SafeDict({k: _coerce(v) for k, v in (context or {}).items()})
    try:
        return template.format_map(safe)
    except (ValueError, IndexError):
        # A malformed template (e.g. stray single brace) should not blow up
        # the pipeline — fall back to the raw template text.
        return template
