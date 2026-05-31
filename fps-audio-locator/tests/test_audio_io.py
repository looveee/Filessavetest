"""Tests for audio probing and validation (core/audio_io.py)."""

from __future__ import annotations

import pytest

from core.audio_io import compute_sha256, probe_audio, validate_probe
from core.errors import AudioImportError


def test_probe_reports_metadata(make_wav):
    wav = make_wav(sample_rate=48000, channels=2, duration_s=1.0)
    probe = probe_audio(wav)
    assert probe["channels"] == 2
    assert probe["sample_rate"] == 48000
    assert probe["num_frames"] == 48000
    assert probe["duration_s"] == pytest.approx(1.0)
    assert probe["format"] == "wav"
    assert len(probe["sha256"]) == 64
    assert probe["size_bytes"] > 0


def test_probe_missing_file_raises():
    with pytest.raises(AudioImportError):
        probe_audio("/nope/missing.wav")


def test_valid_audio_passes(make_wav, config):
    probe = probe_audio(make_wav())
    validate_probe(probe, config.audio)  # should not raise


def test_mono_rejected(make_wav, config):
    probe = probe_audio(make_wav(channels=1))
    with pytest.raises(AudioImportError, match="stereo"):
        validate_probe(probe, config.audio)


def test_bad_sample_rate_rejected(make_wav, config):
    probe = probe_audio(make_wav(sample_rate=16000))
    with pytest.raises(AudioImportError, match="sample_rate"):
        validate_probe(probe, config.audio)


def test_too_short_rejected(make_wav, config):
    probe = probe_audio(make_wav(duration_s=0.01))
    with pytest.raises(AudioImportError, match="duration"):
        validate_probe(probe, config.audio)


def test_sha256_is_stable(make_wav):
    wav = make_wav()
    assert compute_sha256(wav) == compute_sha256(wav)
