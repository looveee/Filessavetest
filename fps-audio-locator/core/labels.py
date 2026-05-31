"""Label loading and semantic validation.

Two distinct checks happen for every label:

1. **Structural** validation — does the JSON match :class:`SampleLabel`?
   Handled by pydantic in :func:`load_label` / :func:`parse_label`.
2. **Semantic** validation — are the values legal *for this project*? e.g. is
   the map known, the sound type allowed, the position inside the map bounds,
   the capture origin within the compliance scope? Handled by
   :func:`validate_label` against the :class:`AppConfig`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.config import AppConfig
from core.errors import LabelValidationError, SchemaValidationError
from core.schema import SampleLabel


def parse_label(payload: dict[str, Any]) -> SampleLabel:
    """Validate a dict against the label schema."""
    try:
        return SampleLabel.model_validate(payload)
    except ValidationError as exc:
        raise SchemaValidationError(f"Label does not match schema:\n{exc}") from exc


def load_label(path: str | Path) -> SampleLabel:
    """Read a label JSON file and structurally validate it."""
    path = Path(path)
    if not path.is_file():
        raise SchemaValidationError(f"Label file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"Label is not valid JSON ({path}): {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaValidationError(
            f"Label root must be a JSON object, got {type(payload).__name__}"
        )
    return parse_label(payload)


def check_label(label: SampleLabel, config: AppConfig) -> list[str]:
    """Return a list of semantic issues; empty list means the label is valid."""
    issues: list[str] = []
    labels_cfg = config.labels

    if label.map_id not in labels_cfg.allowed_maps:
        issues.append(
            f"map_id={label.map_id!r} is not in allowed_maps "
            f"{labels_cfg.allowed_maps}"
        )

    if label.source.sound_type not in labels_cfg.allowed_sound_types:
        issues.append(
            f"source.sound_type={label.source.sound_type!r} is not in "
            f"allowed_sound_types {labels_cfg.allowed_sound_types}"
        )

    if label.capture_origin not in config.project.usage_scope:
        issues.append(
            f"capture_origin={label.capture_origin!r} is outside the project "
            f"usage_scope {config.project.usage_scope}"
        )

    # Position bounds checks (only when bounds are defined for the map).
    bounds = labels_cfg.map_bounds.get(label.map_id)
    if bounds is not None:
        if not bounds.contains(label.listener.position.as_tuple()):
            issues.append(
                f"listener.position {label.listener.position.as_tuple()} is "
                f"outside map bounds for {label.map_id}"
            )
        if not bounds.contains(label.source.position.as_tuple()):
            issues.append(
                f"source.position {label.source.position.as_tuple()} is "
                f"outside map bounds for {label.map_id}"
            )

    return issues


def validate_label(label: SampleLabel, config: AppConfig) -> None:
    """Raise :class:`LabelValidationError` if the label violates any rule."""
    issues = check_label(label, config)
    if issues:
        raise LabelValidationError(
            f"Label failed {len(issues)} validation rule(s)", issues=issues
        )
