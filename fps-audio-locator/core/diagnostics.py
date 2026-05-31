"""Diagnostic plots for a sample (Phase 2).

Generates four PNGs into ``<diagnostics_dir>/<sample_id>/`` so a human can eye
whether a capture is sane *before* it ever feeds a model:

* ``waveform.png``    — left/right time-domain waveforms.
* ``spectrogram.png`` — linear-frequency STFT magnitude (dB).
* ``mel.png``         — log-mel spectrogram (the modeling front-end).
* ``stereo_diff.png`` — left-right difference signal, annotated with ITD/ILD.

Matplotlib uses the non-interactive ``Agg`` backend so this works headless.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: must be set before pyplot import

import librosa
import librosa.display
import matplotlib.pyplot as plt
import numpy as np

from core.config import AppConfig
from core.features import extract_audio_features, load_analysis_audio

PLOT_NAMES = ("waveform.png", "spectrogram.png", "mel.png", "stereo_diff.png")


def generate_diagnostics(
    sample_id: str, audio_path: str | Path, config: AppConfig
) -> list[Path]:
    """Render the diagnostic plot set for one sample. Returns the PNG paths."""
    fcfg = config.features
    out_dir = config.paths.diagnostics_dir / sample_id
    out_dir.mkdir(parents=True, exist_ok=True)

    binaural_stereo, mono, sr = load_analysis_audio(audio_path, config)
    left, right = binaural_stereo[0], binaural_stereo[1]
    features = extract_audio_features(audio_path, config)

    paths: list[Path] = []
    paths.append(_plot_waveform(left, right, sr, out_dir))
    paths.append(_plot_spectrogram(mono, sr, fcfg.n_fft, fcfg.hop_length, out_dir))
    paths.append(_plot_mel(features["log_mel_spectrogram"], sr, fcfg.hop_length, fcfg.fmax, out_dir))
    paths.append(
        _plot_stereo_diff(
            features["stereo_channel_diff"],
            sr,
            features["itd_estimate_ms"],
            features["ild_db"],
            out_dir,
        )
    )
    return paths


def _times(n: int, sr: int) -> np.ndarray:
    return np.arange(n) / sr


def _plot_waveform(left: np.ndarray, right: np.ndarray, sr: int, out_dir: Path) -> Path:
    t = _times(len(left), sr)
    fig, axes = plt.subplots(2, 1, figsize=(10, 4), sharex=True)
    axes[0].plot(t, left, color="tab:blue", linewidth=0.6)
    axes[0].set_ylabel("Left")
    axes[0].set_title("Waveform (per channel)")
    axes[1].plot(t, right, color="tab:red", linewidth=0.6)
    axes[1].set_ylabel("Right")
    axes[1].set_xlabel("Time (s)")
    for ax in axes:
        ax.grid(True, alpha=0.3)
    return _save(fig, out_dir / "waveform.png")


def _plot_spectrogram(mono: np.ndarray, sr: int, n_fft: int, hop: int, out_dir: Path) -> Path:
    stft_db = librosa.amplitude_to_db(
        np.abs(librosa.stft(mono, n_fft=n_fft, hop_length=hop)), ref=np.max
    )
    fig, ax = plt.subplots(figsize=(10, 4))
    img = librosa.display.specshow(
        stft_db, sr=sr, hop_length=hop, x_axis="time", y_axis="hz", ax=ax
    )
    ax.set_title("STFT magnitude (dB)")
    fig.colorbar(img, ax=ax, format="%+2.0f dB")
    return _save(fig, out_dir / "spectrogram.png")


def _plot_mel(log_mel: np.ndarray, sr: int, hop: int, fmax: float, out_dir: Path) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4))
    img = librosa.display.specshow(
        log_mel, sr=sr, hop_length=hop, x_axis="time", y_axis="mel", fmax=fmax, ax=ax
    )
    ax.set_title("Log-mel spectrogram")
    fig.colorbar(img, ax=ax, format="%+2.0f dB")
    return _save(fig, out_dir / "mel.png")


def _plot_stereo_diff(
    diff: np.ndarray, sr: int, itd_ms: float, ild_db: float, out_dir: Path
) -> Path:
    t = _times(len(diff), sr)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, diff, color="tab:purple", linewidth=0.6)
    ax.set_title(
        f"Stereo difference (L-R)   |   ITD={itd_ms:+.3f} ms   ILD={ild_db:+.2f} dB"
    )
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("L - R amplitude")
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir / "stereo_diff.png")


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path
