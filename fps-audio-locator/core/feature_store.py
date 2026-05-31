"""Persistence of extracted features as compressed ``.npz`` archives.

Layout: ``<features_dir>/<sample_id>.npz``. Each archive holds the full feature
dict from :func:`core.features.extract_audio_features`. Scalars are stored as
0-d arrays by ``np.savez`` and converted back to Python floats/ints on load so
callers get the same dict shape they saved.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.config import PathsConfig
from core.features import FEATURE_KEYS

# Keys that should come back as plain Python scalars rather than 0-d arrays.
_SCALAR_KEYS = {"itd_estimate_ms", "ild_db", "duration_sec", "sample_rate", "channels"}
_INT_KEYS = {"sample_rate", "channels"}


def feature_path(sample_id: str, paths: PathsConfig) -> Path:
    """Return the ``.npz`` path for a sample id."""
    return paths.features_dir / f"{sample_id}.npz"


def save_features(sample_id: str, features: dict, paths: PathsConfig) -> Path:
    """Save a feature dict to ``<features_dir>/<sample_id>.npz`` (compressed)."""
    missing = [k for k in FEATURE_KEYS if k not in features]
    if missing:
        raise ValueError(f"feature dict is missing keys: {missing}")
    dest = feature_path(sample_id, paths)
    dest.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(dest, **features)
    return dest


def load_features(source: str | Path, paths: PathsConfig | None = None) -> dict:
    """Load a feature dict.

    ``source`` may be a direct path to a ``.npz`` file, or a ``sample_id`` when
    ``paths`` is supplied.
    """
    path = Path(source)
    if path.suffix != ".npz":
        if paths is None:
            raise ValueError(
                "load_features needs a PathsConfig when given a sample_id "
                "instead of an .npz path"
            )
        path = feature_path(str(source), paths)
    if not path.is_file():
        raise FileNotFoundError(f"Feature file not found: {path}")

    out: dict = {}
    with np.load(path, allow_pickle=False) as npz:
        for key in npz.files:
            value = npz[key]
            if key in _SCALAR_KEYS:
                out[key] = int(value) if key in _INT_KEYS else float(value)
            else:
                out[key] = value
    return out
