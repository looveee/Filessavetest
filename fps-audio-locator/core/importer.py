"""End-to-end sample import pipeline.

Ties together label validation, audio probing/validation, duplicate detection
and manifest persistence. The CLI is a thin wrapper around
:func:`import_sample`; future callers (API, batch tools) reuse the same path.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from core.audio_io import import_audio, probe_audio, validate_probe
from core.config import AppConfig
from core.errors import DuplicateSampleError
from core.labels import load_label, validate_label
from core.manifest import ManifestStore
from core.schema import SampleLabel, SampleRecord


def make_sample_id(label: SampleLabel, sha256: str) -> str:
    """Deterministic, human-readable, collision-resistant id.

    Form: ``<map_id>_<sound_type>_<sha8>``. Deriving the suffix from the audio
    hash means re-importing identical content yields the same id, which makes
    duplicate detection robust.
    """
    return f"{label.map_id}_{label.source.sound_type}_{sha256[:8]}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def import_sample(
    audio_path: str | Path,
    label_path: str | Path,
    config: AppConfig,
) -> SampleRecord:
    """Validate and import a single (audio, label) pair into the manifest.

    Steps:
        1. Load + structurally validate the label.
        2. Semantically validate the label against the config.
        3. Probe + validate the audio against the config.
        4. Detect duplicates by audio checksum (honoring ``import.on_duplicate``).
        5. Copy audio into the data dir and append a record to the manifest.

    Returns the persisted :class:`SampleRecord`. If the sample is a duplicate
    and the policy is ``skip``, the existing record is returned unchanged.
    """
    audio_path = Path(audio_path)
    label_path = Path(label_path)

    # 1 + 2: label
    label = load_label(label_path)
    validate_label(label, config)

    # 3: audio
    probe = probe_audio(audio_path)
    validate_probe(probe, config.audio)
    sha256 = probe["sha256"]
    sample_id = make_sample_id(label, sha256)

    # 4: duplicate handling
    manifest = ManifestStore(config.paths.manifest_path)
    existing = manifest.find_by_checksum(sha256)
    overwrite_audio = False
    if existing is not None:
        policy = config.import_.on_duplicate
        if policy == "error":
            raise DuplicateSampleError(
                f"Audio already imported as sample {existing.sample_id!r} "
                f"(sha256={sha256[:12]}…). Set import.on_duplicate to "
                f"'skip' or 'overwrite' to change this behavior."
            )
        if policy == "skip":
            return existing
        # overwrite: fall through and re-import, replacing the audio file.
        overwrite_audio = True

    # 5: persist audio + label copy + manifest record
    audio_meta = import_audio(
        audio_path,
        sample_id=sample_id,
        probe=probe,
        paths=config.paths,
        copy=config.import_.copy_audio,
        overwrite=overwrite_audio,
    )

    label_source_path = _archive_label(label_path, sample_id, config)

    record = SampleRecord(
        sample_id=sample_id,
        created_at=_utc_now_iso(),
        audio=audio_meta,
        label=label,
        label_source_path=label_source_path,
    )
    manifest.append(record)
    return record


def _archive_label(label_path: Path, sample_id: str, config: AppConfig) -> str:
    """Copy the original label JSON next to the dataset for provenance.

    Returns the original source path (for the record); the archived copy is a
    side effect. If copying is disabled we just record the source path.
    """
    if not config.import_.copy_audio:
        return str(label_path.resolve())

    config.paths.label_dir.mkdir(parents=True, exist_ok=True)
    dest = config.paths.label_dir / f"{sample_id}.json"
    shutil.copy2(label_path, dest)
    return str(label_path.resolve())
