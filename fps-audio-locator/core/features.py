"""Audio feature extraction (Phase 2).

Turns a validated stereo capture into a standard set of features suitable for
later modeling — *without* training anything here. The emphasis is on:

* **Monaural spectral descriptors** (mel, MFCC, RMS, ZCR, spectral centroid /
  bandwidth / flux) computed on the channel-mean signal, and
* **Binaural cues** that carry directional information: the inter-channel time
  difference (ITD, via cross-correlation) and inter-channel level difference
  (ILD, an energy ratio in dB), plus the raw stereo difference signal.

Everything is parameterized through ``config.features`` so the analysis is
reproducible and comparable across captures.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import correlate

from core.config import AppConfig, FeaturesConfig
from core.errors import FeatureExtractionError

# Canonical ordering of the feature dictionary keys produced below. Used by the
# feature store and tests to assert completeness.
FEATURE_KEYS: tuple[str, ...] = (
    "log_mel_spectrogram",
    "mfcc",
    "rms_energy",
    "zero_crossing_rate",
    "spectral_centroid",
    "spectral_bandwidth",
    "spectral_flux",
    "stereo_channel_diff",
    "itd_estimate_ms",
    "ild_db",
    "duration_sec",
    "sample_rate",
    "channels",
)

_EPS = 1e-10


def _load_stereo(audio_path: str | Path) -> tuple[np.ndarray, int]:
    """Load audio as a (channels, frames) float32 array at native sample rate."""
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FeatureExtractionError(f"Audio file not found: {audio_path}")
    try:
        data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
    except Exception as exc:  # pragma: no cover - defensive
        raise FeatureExtractionError(f"Cannot read audio {audio_path}: {exc}") from exc
    # soundfile returns (frames, channels); transpose to (channels, frames).
    return data.T, sr


def _peak_normalize(y: np.ndarray) -> np.ndarray:
    """Peak-normalize a 1-D signal to [-1, 1]."""
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > _EPS:
        return y / peak
    return y


def _normalize_stereo(stereo: np.ndarray, mode: str) -> np.ndarray:
    """Normalize a (2, frames) stereo signal according to ``mode``.

    * ``none``             -> unchanged.
    * ``global_peak``      -> divide BOTH channels by their shared peak. This is
      a common gain, so the inter-channel level ratio (and thus ILD) is
      preserved.
    * ``per_channel_peak`` -> normalize each channel independently. This makes
      both channels reach full scale and therefore *destroys* the level
      difference between them. It must never be used for ITD/ILD.
    """
    if mode == "none":
        return stereo
    if mode == "global_peak":
        peak = float(np.max(np.abs(stereo))) if stereo.size else 0.0
        return stereo / peak if peak > _EPS else stereo
    if mode == "per_channel_peak":
        return np.stack([_peak_normalize(ch) for ch in stereo])
    raise FeatureExtractionError(f"Unknown normalize_mode: {mode!r}")


def _estimate_itd_ms(left: np.ndarray, right: np.ndarray, sr: int, max_lag_ms: float) -> float:
    """Estimate inter-channel time difference via cross-correlation.

    The lag (in samples) that maximizes the cross-correlation between the left
    and right channels, searched within +/- ``max_lag_ms``, is converted to
    milliseconds. Sign convention: a **positive** value means the right channel
    is delayed relative to the left (sound arrives at the left ear first, i.e.
    the source is on the listener's left).
    """
    max_lag = max(1, int(round(max_lag_ms / 1000.0 * sr)))
    # Full cross-correlation; FFT method keeps this cheap for clips up to ~30s.
    # correlate(right, left) peaks at the lag by which `right` trails `left`, so
    # a positive lag means the right channel is delayed (sound reaches the left
    # first => source on the listener's left).
    corr = correlate(right, left, mode="full", method="fft")
    zero_lag = len(left) - 1  # index in `corr` corresponding to lag 0
    lo = zero_lag - max_lag
    hi = zero_lag + max_lag + 1
    window = corr[lo:hi]
    if window.size == 0:
        return 0.0
    best = int(np.argmax(window)) - max_lag
    return float(best) / sr * 1000.0


def _ild_db(left: np.ndarray, right: np.ndarray) -> float:
    """Inter-channel level difference in dB (positive => left louder)."""
    e_left = float(np.sum(left**2))
    e_right = float(np.sum(right**2))
    return float(10.0 * np.log10((e_left + _EPS) / (e_right + _EPS)))


def _spectral_flux(mono: np.ndarray, n_fft: int, hop_length: int) -> np.ndarray:
    """Half-wave-rectified spectral flux per frame (onset-like novelty curve)."""
    mag = np.abs(librosa.stft(mono, n_fft=n_fft, hop_length=hop_length))
    diff = np.diff(mag, axis=1)
    rectified = np.maximum(diff, 0.0)
    flux = np.sum(rectified, axis=0)
    # Prepend 0 so length matches the number of frames.
    return np.concatenate([[0.0], flux]).astype(np.float32)


def load_analysis_audio(
    audio_path: str | Path, config: AppConfig
) -> tuple[np.ndarray, np.ndarray, int]:
    """Load a stereo file as analysis-ready audio.

    Returns ``(binaural_stereo, mono, sr)`` where:

    * ``binaural_stereo`` -- ``(2, frames)`` float32 used for the binaural cues
      (ITD / ILD / stereo difference). It is normalized with a **binaural-safe**
      mode only: ``none`` or ``global_peak``. If ``normalize_mode`` is
      ``per_channel_peak`` it is downgraded to ``global_peak`` here, because
      per-channel normalization destroys the inter-channel level ratio.
    * ``mono`` -- ``(frames,)`` float32 channel mean used for the monaural
      spectral features. It may be peak-normalized (scale-invariant features are
      unaffected; RMS reflects the chosen mode).
    * ``sr`` -- the analysis sample rate.

    This is the single place where the raw capture is turned into the signals
    that both feature extraction and diagnostic plotting operate on, so the two
    never diverge.

    Raises:
        FeatureExtractionError: if the audio is not 2-channel.
    """
    fcfg: FeaturesConfig = config.features
    channels_audio, native_sr = _load_stereo(audio_path)
    n_channels = channels_audio.shape[0]

    if n_channels != 2:
        raise FeatureExtractionError(
            f"Stereo (2-channel) audio is required for binaural features, got "
            f"{n_channels} channel(s) in {audio_path}. ITD/ILD are undefined for "
            f"mono input — fix the capture rather than degrading silently."
        )

    # Resample to the configured analysis rate so features are comparable.
    if native_sr != fcfg.sample_rate:
        channels_audio = np.stack(
            [
                librosa.resample(ch, orig_sr=native_sr, target_sr=fcfg.sample_rate)
                for ch in channels_audio
            ]
        )
    sr = fcfg.sample_rate

    # Binaural cues must never see a per-channel-normalized signal: that would
    # flatten ILD to ~0. Downgrade per_channel_peak -> global_peak here.
    binaural_mode = (
        "global_peak"
        if fcfg.normalize_mode == "per_channel_peak"
        else fcfg.normalize_mode
    )
    binaural_stereo = _normalize_stereo(channels_audio, binaural_mode).astype(np.float32)

    # Mono path for spectral features: normalization is fine here.
    mono = np.mean(channels_audio, axis=0)
    if fcfg.normalize_mode != "none":
        mono = _peak_normalize(mono)
    mono = mono.astype(np.float32)

    return binaural_stereo, mono, sr


def extract_audio_features(audio_path: str | Path, config: AppConfig) -> dict:
    """Extract the standard Phase-2 feature set from a stereo audio file.

    Args:
        audio_path: Path to a 2-channel audio file.
        config: Loaded :class:`AppConfig`; ``config.features`` drives the STFT /
            mel / MFCC parameters and the ITD search window.

    Returns:
        Dict keyed by :data:`FEATURE_KEYS`. Array features are ``np.float32``;
        scalars are plain Python ``float``/``int``.

    Raises:
        FeatureExtractionError: if the audio is not stereo (no silent
            degradation of binaural cues) or cannot be read.
    """
    fcfg: FeaturesConfig = config.features
    binaural_stereo, mono, sr = load_analysis_audio(audio_path, config)
    n_channels = binaural_stereo.shape[0]

    # Binaural cues come from the binaural-safe stereo (ILD-preserving).
    left, right = binaural_stereo[0], binaural_stereo[1]
    duration_sec = float(mono.shape[0]) / sr

    # --- monaural spectral descriptors ---------------------------------
    mel = librosa.feature.melspectrogram(
        y=mono,
        sr=sr,
        n_fft=fcfg.n_fft,
        hop_length=fcfg.hop_length,
        n_mels=fcfg.n_mels,
        fmin=fcfg.fmin,
        fmax=fcfg.fmax,
        power=2.0,
    )
    log_mel = librosa.power_to_db(mel, ref=np.max).astype(np.float32)

    mfcc = librosa.feature.mfcc(
        S=librosa.power_to_db(mel), n_mfcc=fcfg.n_mfcc
    ).astype(np.float32)

    rms = librosa.feature.rms(
        y=mono, frame_length=fcfg.n_fft, hop_length=fcfg.hop_length
    )[0].astype(np.float32)

    zcr = librosa.feature.zero_crossing_rate(
        y=mono, frame_length=fcfg.n_fft, hop_length=fcfg.hop_length
    )[0].astype(np.float32)

    centroid = librosa.feature.spectral_centroid(
        y=mono, sr=sr, n_fft=fcfg.n_fft, hop_length=fcfg.hop_length
    )[0].astype(np.float32)

    bandwidth = librosa.feature.spectral_bandwidth(
        y=mono, sr=sr, n_fft=fcfg.n_fft, hop_length=fcfg.hop_length
    )[0].astype(np.float32)

    flux = _spectral_flux(mono, fcfg.n_fft, fcfg.hop_length)

    # --- binaural cues -------------------------------------------------
    stereo_diff = (left - right).astype(np.float32)
    itd_ms = _estimate_itd_ms(left, right, sr, fcfg.itd_max_lag_ms)
    ild = _ild_db(left, right)

    return {
        "log_mel_spectrogram": log_mel,
        "mfcc": mfcc,
        "rms_energy": rms,
        "zero_crossing_rate": zcr,
        "spectral_centroid": centroid,
        "spectral_bandwidth": bandwidth,
        "spectral_flux": flux,
        "stereo_channel_diff": stereo_diff,
        "itd_estimate_ms": float(itd_ms),
        "ild_db": float(ild),
        "duration_sec": float(duration_sec),
        "sample_rate": int(sr),
        "channels": int(n_channels),
    }
