"""Tests for the data sample schema (core/schema.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.schema import SampleLabel, Vec3


def test_valid_label_parses(valid_label_dict):
    label = SampleLabel.model_validate(valid_label_dict)
    assert label.map_id == "de_dust2"
    assert label.source.sound_type == "footstep"
    assert label.listener.position.as_tuple() == (250.0, -120.0, 64.0)
    assert label.schema_version == "1.0"


def test_schema_version_defaults(valid_label_dict):
    valid_label_dict.pop("schema_version")
    label = SampleLabel.model_validate(valid_label_dict)
    assert label.schema_version == "1.0"


def test_optional_fields_default(valid_label_dict):
    for key in ("weapon", "game_build", "notes", "extra"):
        valid_label_dict.pop(key, None)
    label = SampleLabel.model_validate(valid_label_dict)
    assert label.weapon is None
    assert label.extra == {}


@pytest.mark.parametrize("missing", ["map_id", "listener", "source", "capture_origin"])
def test_missing_required_field_rejected(valid_label_dict, missing):
    valid_label_dict.pop(missing)
    with pytest.raises(ValidationError):
        SampleLabel.model_validate(valid_label_dict)


def test_unknown_top_level_field_rejected(valid_label_dict):
    valid_label_dict["totally_unexpected"] = 123
    with pytest.raises(ValidationError):
        SampleLabel.model_validate(valid_label_dict)


def test_unknown_nested_field_rejected(valid_label_dict):
    valid_label_dict["source"]["bogus"] = True
    with pytest.raises(ValidationError):
        SampleLabel.model_validate(valid_label_dict)


def test_vec3_requires_all_components():
    with pytest.raises(ValidationError):
        Vec3.model_validate({"x": 1.0, "y": 2.0})


def test_vec3_coerces_numeric_strings():
    v = Vec3.model_validate({"x": "1.5", "y": 2, "z": -3})
    assert v.as_tuple() == (1.5, 2.0, -3.0)


def test_non_numeric_position_rejected(valid_label_dict):
    valid_label_dict["source"]["position"]["x"] = "not-a-number"
    with pytest.raises(ValidationError):
        SampleLabel.model_validate(valid_label_dict)


def test_round_trip_json(valid_label_dict):
    label = SampleLabel.model_validate(valid_label_dict)
    restored = SampleLabel.model_validate_json(label.model_dump_json())
    assert restored == label
