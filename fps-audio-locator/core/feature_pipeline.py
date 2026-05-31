"""Orchestration that connects the manifest to feature extraction.

Resolves a sample's stored audio, extracts features and persists them to the
feature store. Kept separate from the CLI so the API and batch jobs reuse it.
"""

from __future__ import annotations

from pathlib import Path

from core.config import AppConfig
from core.errors import FeatureExtractionError
from core.features import extract_audio_features
from core.feature_store import save_features
from core.manifest import ManifestStore
from core.schema import SampleRecord


def audio_abs_path(record: SampleRecord, config: AppConfig) -> Path:
    """Resolve a record's audio path (relative to data_root) to an absolute path."""
    p = Path(record.audio.path)
    return p if p.is_absolute() else (config.paths.data_root / p)


def extract_and_store(sample_id: str, config: AppConfig) -> Path:
    """Extract features for one sample id and save the ``.npz``. Returns its path."""
    store = ManifestStore(config.paths.manifest_path)
    record = store.get(sample_id)
    if record is None:
        raise FeatureExtractionError(
            f"sample_id {sample_id!r} not found in manifest "
            f"{config.paths.manifest_path}"
        )
    audio_path = audio_abs_path(record, config)
    features = extract_audio_features(audio_path, config)
    return save_features(sample_id, features, config.paths)


def extract_all(config: AppConfig) -> list[tuple[str, Path]]:
    """Extract + store features for every sample in the manifest."""
    store = ManifestStore(config.paths.manifest_path)
    results: list[tuple[str, Path]] = []
    for record in store.iter_records():
        audio_path = audio_abs_path(record, config)
        features = extract_audio_features(audio_path, config)
        npz = save_features(record.sample_id, features, config.paths)
        results.append((record.sample_id, npz))
    return results
