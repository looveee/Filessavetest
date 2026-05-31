"""Audio probing, validation and import.

Phase 1 deliberately does *not* decode or transform audio samples. It only
reads file headers (via ``soundfile``), checks them against the configured
constraints (stereo, sample rate, duration, format) and copies/links the file
into the managed data directory. Decoding, resampling and feature extraction
belong to later phases.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import soundfile as sf

from core.config import AudioConfig, PathsConfig
from core.errors import AudioImportError
from core.schema import AudioMeta

_CHUNK = 1024 * 1024  # 1 MiB read chunks for hashing


def compute_sha256(path: str | Path) -> str:
    """Stream the file and return its hex SHA-256 digest."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_audio(path: str | Path) -> dict:
    """Read header metadata without loading samples into memory.

    Returns a dict with: format, sample_rate, channels, num_frames,
    duration_s, size_bytes, sha256.
    """
    path = Path(path)
    if not path.is_file():
        raise AudioImportError(f"Audio file not found: {path}")

    try:
        info = sf.info(str(path))
    except Exception as exc:  # soundfile raises RuntimeError/LibsndfileError
        raise AudioImportError(f"Cannot read audio file {path}: {exc}") from exc

    duration_s = info.frames / info.samplerate if info.samplerate else 0.0
    fmt = (info.format or path.suffix.lstrip(".")).lower()
    return {
        "format": fmt,
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "num_frames": info.frames,
        "duration_s": round(duration_s, 6),
        "size_bytes": path.stat().st_size,
        "sha256": compute_sha256(path),
    }


def validate_probe(probe: dict, audio_cfg: AudioConfig) -> None:
    """Validate probed metadata against the audio constraints.

    Raises:
        AudioImportError: with all violations collected into one message.
    """
    issues: list[str] = []

    if probe["channels"] != audio_cfg.required_channels:
        issues.append(
            f"channels={probe['channels']} but required_channels="
            f"{audio_cfg.required_channels} (stereo is required)"
        )
    if probe["sample_rate"] not in audio_cfg.allowed_sample_rates:
        issues.append(
            f"sample_rate={probe['sample_rate']} not in allowed "
            f"{audio_cfg.allowed_sample_rates}"
        )
    if probe["format"] not in audio_cfg.allowed_formats:
        issues.append(
            f"format={probe['format']!r} not in allowed {audio_cfg.allowed_formats}"
        )
    dur = probe["duration_s"]
    if dur < audio_cfg.min_duration_s:
        issues.append(f"duration {dur}s < min {audio_cfg.min_duration_s}s")
    if dur > audio_cfg.max_duration_s:
        issues.append(f"duration {dur}s > max {audio_cfg.max_duration_s}s")

    if issues:
        raise AudioImportError(
            "Audio failed validation:\n  - " + "\n  - ".join(issues)
        )


def import_audio(
    src: str | Path,
    *,
    sample_id: str,
    probe: dict,
    paths: PathsConfig,
    copy: bool = True,
    overwrite: bool = False,
) -> AudioMeta:
    """Place the audio under the managed data dir and return its metadata.

    The destination filename is ``<sample_id>.<format>`` so files are
    self-describing and collision-free. ``path`` in the returned
    :class:`AudioMeta` is stored relative to ``data_root`` for portability.
    """
    src = Path(src)
    fmt = probe["format"]
    paths.audio_dir.mkdir(parents=True, exist_ok=True)
    dest = paths.audio_dir / f"{sample_id}.{fmt}"

    if dest.exists() and not overwrite:
        raise AudioImportError(f"Destination already exists: {dest}")

    if copy:
        shutil.copy2(src, dest)
        stored = dest
    else:
        # Reference in place; record the absolute source path.
        stored = src.resolve()

    try:
        rel_path = stored.relative_to(paths.data_root.resolve())
        path_str = str(rel_path)
    except ValueError:
        # Outside the data root (e.g. reference-in-place); store absolute.
        path_str = str(stored)

    return AudioMeta(
        path=path_str,
        format=fmt,
        sample_rate=probe["sample_rate"],
        channels=probe["channels"],
        num_frames=probe["num_frames"],
        duration_s=probe["duration_s"],
        sha256=probe["sha256"],
        size_bytes=probe["size_bytes"],
    )
