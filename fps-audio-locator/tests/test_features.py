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


def _write_louder_left_stereo(path, *, sr=48000, dur=1.0, freq=440.0,
                              left_amp=0.6, right_amp=0.15):
    """Stereo with the left channel clearly louder than the right, no delay.

    Pure level difference: ITD ~= 0, and ILD should be strongly positive
    (10*log10(left_amp^2 / right_amp^2)). With left_amp=0.6, right_amp=0.15 the
    expected ILD is ~12 dB.
    """
    n = int(sr * dur)
    t = np.arange(n) / sr
    tone = np.sin(2 * np.pi * freq * t)
    left = left_amp * tone
    right = right_amp * tone
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


def test_ild_positive_for_louder_left(tmp_path, config):
    # Regression guard for the ILD bug: per-channel peak normalization used to
    # flatten this to ~0 dB. With the global_peak default the level difference
    # must survive.
    wav = tmp_path / "louder_left.wav"
    _write_louder_left_stereo(wav, sr=config.features.sample_rate,
                              left_amp=0.6, right_amp=0.15)
    feats = extract_audio_features(wav, config)
    # Expected ~ 10*log10(0.6^2 / 0.15^2) = ~12 dB.
    assert feats["ild_db"] > 6.0, feats["ild_db"]
    assert abs(feats["ild_db"]) > 1.0  # explicitly NOT near zero
    # No delay -> ITD should be ~0.
    assert abs(feats["itd_estimate_ms"]) < 0.05


def test_ild_preserved_even_when_mode_is_per_channel_peak(tmp_path, config):
    # Even if the operator selects per_channel_peak, binaural cues must fall
    # back to a binaural-safe signal so ILD is not destroyed.
    config.features.normalize_mode = "per_channel_peak"
    wav = tmp_path / "louder_left.wav"
    _write_louder_left_stereo(wav, sr=config.features.sample_rate,
                              left_amp=0.6, right_amp=0.15)
    feats = extract_audio_features(wav, config)
    assert feats["ild_db"] > 6.0, feats["ild_db"]


def test_ild_consistent_across_global_and_none_modes(tmp_path, config):
    # ITD/ILD are gain-invariant, so none vs global_peak must give the same cue.
    wav = tmp_path / "louder_left.wav"
    _write_louder_left_stereo(wav, sr=config.features.sample_rate)

    config.features.normalize_mode = "none"
    none_feats = extract_audio_features(wav, config)
    config.features.normalize_mode = "global_peak"
    global_feats = extract_audio_features(wav, config)

    assert none_feats["ild_db"] == pytest.approx(global_feats["ild_db"], abs=1e-4)
    assert none_feats["itd_estimate_ms"] == pytest.approx(
        global_feats["itd_estimate_ms"], abs=1e-6
    )


def test_per_channel_peak_helper_flattens_ild():
    # Documents *why* per_channel_peak is forbidden for binaural cues: it makes
    # both channels full-scale and so erases the level difference.
    from core.features import _ild_db, _normalize_stereo

    left = 0.6 * np.ones(1000, dtype=np.float32)
    right = 0.15 * np.ones(1000, dtype=np.float32)
    stereo = np.stack([left, right])

    raw_ild = _ild_db(stereo[0], stereo[1])
    per_ch = _normalize_stereo(stereo, "per_channel_peak")
    flattened_ild = _ild_db(per_ch[0], per_ch[1])
    glob = _normalize_stereo(stereo, "global_peak")
    global_ild = _ild_db(glob[0], glob[1])

    assert raw_ild > 6.0
    assert abs(flattened_ild) < 1e-6          # destroyed
    assert global_ild == pytest.approx(raw_ild, abs=1e-4)  # preserved


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
