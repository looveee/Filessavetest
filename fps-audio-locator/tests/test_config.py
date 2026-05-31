"""Tests for the configuration loader (core/config.py)."""

from __future__ import annotations

import pytest

from core.config import load_config
from core.errors import ConfigError


def test_load_resolves_paths_absolute(config_path):
    cfg = load_config(config_path)
    # Relative paths in YAML are anchored to the project root (config's parent's
    # parent) and become absolute.
    assert cfg.paths.manifest_path.is_absolute()
    assert cfg.paths.manifest_path.name == "manifest.jsonl"
    assert cfg.paths.data_root.name == "data"


def test_usage_scope_loaded(config):
    assert "selfhost" in config.project.usage_scope
    assert config.audio.required_channels == 2


def test_missing_config_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "configs" / "nope.yaml")


def test_unknown_key_rejected(tmp_path):
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    cfg = cfg_dir / "config.yaml"
    cfg.write_text(
        "project:\n  name: x\n  version: '0'\n  usage_scope: [research]\n"
        "  surprise: 1\n"
        "paths:\n  data_root: data\n  audio_dir: data/raw\n"
        "  label_dir: data/labels\n  manifest_path: data/m.jsonl\n"
        "audio:\n  required_channels: 2\n  allowed_sample_rates: [48000]\n"
        "  resample_target: 48000\n  min_duration_s: 0.1\n  max_duration_s: 30\n"
        "  allowed_formats: [wav]\n"
        "labels:\n  coordinate_system: x\n  allowed_maps: [de_dust2]\n"
        "  allowed_sound_types: [footstep]\n  map_bounds: {}\n"
        "import:\n  copy_audio: true\n  on_duplicate: error\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(cfg)
