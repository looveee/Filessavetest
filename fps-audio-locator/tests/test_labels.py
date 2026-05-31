"""Tests for label loading and semantic validation (core/labels.py)."""

from __future__ import annotations

import pytest

from core.errors import LabelValidationError, SchemaValidationError
from core.labels import check_label, load_label, parse_label, validate_label


def test_valid_label_passes(config, valid_label_dict):
    label = parse_label(valid_label_dict)
    assert check_label(label, config) == []
    validate_label(label, config)  # should not raise


def test_load_label_from_file(config, write_label, valid_label_dict):
    path = write_label(valid_label_dict)
    label = load_label(path)
    validate_label(label, config)


def test_load_missing_file_raises():
    with pytest.raises(SchemaValidationError):
        load_label("/nonexistent/label.json")


def test_load_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{nope", encoding="utf-8")
    with pytest.raises(SchemaValidationError):
        load_label(p)


def test_unknown_map_rejected(config, valid_label_dict):
    valid_label_dict["map_id"] = "de_unknown"
    label = parse_label(valid_label_dict)
    issues = check_label(label, config)
    assert any("map_id" in i for i in issues)
    with pytest.raises(LabelValidationError):
        validate_label(label, config)


def test_unknown_sound_type_rejected(config, valid_label_dict):
    valid_label_dict["source"]["sound_type"] = "laser"
    label = parse_label(valid_label_dict)
    issues = check_label(label, config)
    assert any("sound_type" in i for i in issues)


def test_out_of_scope_capture_origin_rejected(config, valid_label_dict):
    valid_label_dict["capture_origin"] = "ranked_matchmaking"
    label = parse_label(valid_label_dict)
    issues = check_label(label, config)
    assert any("capture_origin" in i for i in issues)
    with pytest.raises(LabelValidationError):
        validate_label(label, config)


def test_position_out_of_bounds_rejected(config, valid_label_dict):
    valid_label_dict["source"]["position"]["x"] = 99999.0
    label = parse_label(valid_label_dict)
    issues = check_label(label, config)
    assert any("source.position" in i for i in issues)


def test_listener_out_of_bounds_rejected(config, valid_label_dict):
    valid_label_dict["listener"]["position"]["y"] = -99999.0
    label = parse_label(valid_label_dict)
    issues = check_label(label, config)
    assert any("listener.position" in i for i in issues)


def test_map_without_bounds_skips_bounds_check(config, valid_label_dict):
    # de_mirage has no map_bounds entry in the test config -> no bounds issue.
    valid_label_dict["map_id"] = "de_mirage"
    valid_label_dict["source"]["position"]["x"] = 1e9
    label = parse_label(valid_label_dict)
    issues = check_label(label, config)
    assert not any("position" in i for i in issues)


def test_multiple_issues_collected(config, valid_label_dict):
    valid_label_dict["map_id"] = "de_unknown"
    valid_label_dict["source"]["sound_type"] = "laser"
    valid_label_dict["capture_origin"] = "ranked"
    label = parse_label(valid_label_dict)
    with pytest.raises(LabelValidationError) as exc:
        validate_label(label, config)
    assert len(exc.value.issues) >= 3
