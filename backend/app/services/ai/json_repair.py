"""Best-effort JSON recovery for LLM output.

LLMs frequently wrap JSON in ```json fences, add prose before/after, or
leave a trailing comma. `repair_loads` tries a small ladder of fixes and
returns a parsed object, or raises ValueError if nothing works.

This is the "repair once" step: the service calls strict json.loads first,
and only falls back here when that fails.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def _extract_braced(text: str) -> str:
    """Pull out the outermost {...} or [...] span, dropping surrounding prose."""
    obj_start, obj_end = text.find("{"), text.rfind("}")
    arr_start, arr_end = text.find("["), text.rfind("]")

    candidates = []
    if obj_start != -1 and obj_end > obj_start:
        candidates.append((obj_start, obj_end))
    if arr_start != -1 and arr_end > arr_start:
        candidates.append((arr_start, arr_end))
    if not candidates:
        return text
    # Prefer whichever bracket appears first in the text.
    start, end = min(candidates, key=lambda se: se[0])
    return text[start:end + 1]


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def repair_loads(text: str) -> Any:
    """Return a parsed JSON object from messy `text`, or raise ValueError."""
    if not text or not text.strip():
        raise ValueError("empty content")

    base = text.strip()
    stripped = _strip_fences(base)
    extracted = _extract_braced(stripped)

    ladder = [
        base,
        stripped,
        extracted,
        _remove_trailing_commas(extracted),
    ]

    last_err: Exception | None = None
    for candidate in ladder:
        try:
            return json.loads(candidate)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise ValueError(f"unrepairable JSON: {last_err}")
