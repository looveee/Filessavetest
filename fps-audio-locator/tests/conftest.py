"""Shared pytest fixtures.

Each test gets an isolated config + data directory under ``tmp_path`` so the
real ``data/`` directory is never touched and tests don't interfere.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core.config import AppConfig, load_config

# A fully valid label dict, used as a baseline that tests mutate.
VALID_LABEL: dict = {
    "schema_version": "1.0",
    "map_id": "de_dust2",
    "capture_origin": "selfhost",
    "listener": {
        "position": {"x": 250.0, "y": -120.0, "z": 64.0},
        "yaw": 90.0,
        "pitch": 0.0,
        "roll": 0.0,
    },
    "source": {
        "position": {"x": 980.0, "y": 540.0, "z": 64.0},
        "sound_type": "footstep",
        "source_id": "enemy_3",
    },
    "weapon": None,
    "game_build": "test-build",
    "notes": "unit-test sample",
    "extra": {"tickrate": 64},
}


@pytest.fixture
def valid_label_dict() -> dict:
    """A fresh deep copy of the canonical valid label for each test."""
    return copy.deepcopy(VALID_LABEL)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """Write a self-contained config.yaml under tmp_path and return its path."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = configs_dir / "config.yaml"
    cfg_file.write_text(_CONFIG_YAML, encoding="utf-8")
    return cfg_file


@pytest.fixture
def config(config_path: Path) -> AppConfig:
    """Loaded, path-resolved AppConfig rooted at the tmp project."""
    return load_config(config_path)


@pytest.fixture
def write_label(tmp_path: Path):
    """Factory: write a label dict to a JSON file and return its path."""

    def _write(label: dict, name: str = "label.json") -> Path:
        p = tmp_path / name
        p.write_text(json.dumps(label), encoding="utf-8")
        return p

    return _write


@pytest.fixture
def make_wav(tmp_path: Path):
    """Factory: create a wav file. Defaults are config-valid (stereo/48k)."""

    def _make(
        name: str = "clip.wav",
        *,
        sample_rate: int = 48000,
        channels: int = 2,
        duration_s: float = 1.0,
        freq: float = 440.0,
    ) -> Path:
        n = int(sample_rate * duration_s)
        t = np.arange(n) / sample_rate
        mono = (0.2 * np.sin(2 * np.pi * freq * t)).astype("float32")
        data = np.stack([mono] * channels, axis=1) if channels > 1 else mono
        p = tmp_path / name
        sf.write(str(p), data, sample_rate, subtype="PCM_16")
        return p

    return _make


# Mirrors configs/config.yaml but anchored so paths resolve under tmp_path.
_CONFIG_YAML = """
project:
  name: fps-audio-locator
  version: 0.1.0
  usage_scope: [selfhost, training, replay, research]

paths:
  data_root: data
  audio_dir: data/raw
  label_dir: data/labels
  manifest_path: data/manifest/manifest.jsonl

audio:
  required_channels: 2
  allowed_sample_rates: [44100, 48000]
  resample_target: 48000
  min_duration_s: 0.1
  max_duration_s: 30.0
  allowed_formats: [wav, flac]

labels:
  coordinate_system: left_handed_z_up_game_units
  allowed_maps: [de_dust2, de_mirage, de_inferno]
  allowed_sound_types: [footstep, gunshot, reload, grenade, jump, door]
  map_bounds:
    de_dust2:
      min: [-2500.0, -2500.0, -500.0]
      max: [2500.0, 2500.0, 500.0]

import:
  copy_audio: true
  on_duplicate: error
"""
