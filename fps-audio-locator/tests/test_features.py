"""Tests for audio feature extraction and the feature store (Phase 2)."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from core.errors import FeatureExtractionError
from core.features import FEATURE_KEYS, extract_audio_features
from core.feature_store import feature_path, load_features, save_features


def _write_delayed_stereo(path, *, sr=48000, dur=1.0, delay_samples=24, freq=440.0):
    """Stereo where the right channel is a delayed, quieter copy of the left.

    A right-channel delay means the sound reaches the left first -> the ITD
    estimate should be positive under our sign convention. The right channel is
    also attenuated -> positive ILD (left louder).
    """
    n = int(sr * dur)
    t = np.arange(n) / sr
    left = 0.5 * np.sin(2 * np.pi * freq * t)
    right = np.zeros_like(left)
    right[delay_samples:] = 0.25 * np.sin(2 * np.pi * freq * t[:-delay_samples])
    sf.write(str(path), np.stack([left, right], axis=1).astype("float32"), sr)


def test_mono_audio_is_rejected(make_wav, config):
    mono = make_wav(channels=1)
    with pytest.raises(FeatureExtractionError, match="Stereo"):
        extract_audio_features(mono, config)


def test_stereo_extraction_has_all_keys(make_wav, config):
    feats = extract_audio_features(make_wav(channels=2), config)
    for key in FEATURE_KEYS:
        assert key in feats, f"missing feature key: {key}"


def test_itd_ild_fields_present_and_typed(make_wav, config):
    feats = extract_audio_features(make_wav(channels=2), config)
    assert isinstance(feats["itd_estimate_ms"], float)
    assert isinstance(feats["ild_db"], float)
    assert isinstance(feats["sample_rate"], int)
    assert isinstance(feats["channels"], int)
    assert feats["channels"] == 2
    assert feats["sample_rate"] == config.features.sample_rate


def test_feature_shapes_are_consistent(make_wav, config):
    feats = extract_audio_features(make_wav(channels=2, duration_s=1.0), config)
    n_frames = feats["log_mel_spectrogram"].shape[1]
    assert feats["log_mel_spectrogram"].shape[0] == config.features.n_mels
    assert feats["mfcc"].shape[0] == config.features.n_mfcc
    # Frame-wise features share the time axis with the mel spectrogram.
    for key in ("rms_energy", "zero_crossing_rate", "spectral_centroid",
                "spectral_bandwidth", "spectral_flux"):
        assert feats[key].shape[0] == n_frames, key


def test_itd_positive_for_right_delay(tmp_path, config):
    wav = tmp_path / "delayed.wav"
    _write_delayed_stereo(wav, sr=config.features.sample_rate, delay_samples=24)
    feats = extract_audio_features(wav, config)
    # 24 samples @ 48k ~= 0.5 ms, and right is delayed -> positive ITD.
    assert feats["itd_estimate_ms"] > 0.1
    # Right channel attenuated -> left louder -> positive ILD.
    assert feats["ild_db"] > 0.0


def test_identical_channels_give_near_zero_cues(make_wav, config):
    # make_wav duplicates the mono signal across channels -> itd 0, ild ~0.
    feats = extract_audio_features(make_wav(channels=2), config)
    assert abs(feats["itd_estimate_ms"]) < 1e-6
    assert abs(feats["ild_db"]) < 1e-3


def test_save_and_load_roundtrip(make_wav, config):
    feats = extract_audio_features(make_wav(channels=2), config)
    npz = save_features("sample_x", feats, config.paths)
    assert npz == feature_path("sample_x", config.paths)
    assert npz.is_file()

    loaded = load_features(npz)
    assert set(loaded.keys()) == set(FEATURE_KEYS)
    np.testing.assert_allclose(
        loaded["log_mel_spectrogram"], feats["log_mel_spectrogram"]
    )
    assert loaded["itd_estimate_ms"] == pytest.approx(feats["itd_estimate_ms"])
    assert loaded["ild_db"] == pytest.approx(feats["ild_db"])
    assert loaded["sample_rate"] == feats["sample_rate"]
    assert isinstance(loaded["sample_rate"], int)


def test_load_by_sample_id(make_wav, config):
    feats = extract_audio_features(make_wav(channels=2), config)
    save_features("sid", feats, config.paths)
    loaded = load_features("sid", config.paths)
    assert loaded["channels"] == 2


def test_save_missing_keys_raises(config):
    with pytest.raises(ValueError, match="missing keys"):
        save_features("bad", {"itd_estimate_ms": 0.0}, config.paths)


def test_load_missing_file_raises(config):
    with pytest.raises(FileNotFoundError):
        load_features("does_not_exist", config.paths)
