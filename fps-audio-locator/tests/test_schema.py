"""Tests for the data sample schema (core/schema.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.schema import SampleLabel, Vec3


def test_valid_label_parses(valid_label_dict):
    label = SampleLabel.model_validate(valid_label_dict)
    assert label.map_id == "de_dust2"
    assert label.source.sound_type == "footstep_run"
    assert label.listener.position.as_tuple() == (250.0, -120.0, 64.0)
    assert label.schema_version == "1.1"


def test_schema_version_defaults(valid_label_dict):
    valid_label_dict.pop("schema_version")
    label = SampleLabel.model_validate(valid_label_dict)
    assert label.schema_version == "1.1"


def test_optional_fields_default(valid_label_dict):
    for key in ("weapon", "game_build", "notes", "extra"):
        valid_label_dict.pop(key, None)
    label = SampleLabel.model_validate(valid_label_dict)
    assert label.weapon is None
    assert label.extra == {}


def test_training_fields_default_to_none(valid_label_dict):
    # The new training-oriented fields are optional and default to None.
    label = SampleLabel.model_validate(valid_label_dict)
    for field in (
        "listener_point_id", "source_point_id", "action", "material",
        "floor_relation", "distance_m", "occlusion", "area_id",
    ):
        assert getattr(label, field) is None


def test_training_fields_parse_when_present(valid_label_dict):
    valid_label_dict.update(
        {
            "listener_point_id": "ct_spawn_01",
            "source_point_id": "long_doors_03",
            "action": "footstep_run",
            "material": "wood",
            "floor_relation": "same",
            "distance_m": 18.5,
            "occlusion": "partial",
            "area_id": "long_a",
        }
    )
    label = SampleLabel.model_validate(valid_label_dict)
    assert label.listener_point_id == "ct_spawn_01"
    assert label.source_point_id == "long_doors_03"
    assert label.distance_m == 18.5
    assert label.occlusion == "partial"
    assert label.area_id == "long_a"


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
