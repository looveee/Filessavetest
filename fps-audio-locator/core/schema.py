"""Data sample schema.

These pydantic models define the contract for every sample that enters the
system. There are two layers:

* The **label** (:class:`SampleLabel`) — authored by the data collector and
  delivered as a JSON file alongside the audio. It captures the ground truth:
  listener pose, sound source position and metadata.
* The **manifest record** (:class:`SampleRecord`) — produced by the import
  pipeline. It bundles the validated label with probed audio metadata and an
  identity, and is what gets persisted to ``manifest.jsonl``.

All models forbid unknown fields so that mislabeled / mistyped keys fail loudly
during import rather than silently disappearing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Vec3(_Strict):
    """A 3D point/vector in the configured coordinate system (game units)."""

    x: float
    y: float
    z: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


class ListenerPose(_Strict):
    """Where the listener (the local player) is and where they face.

    Orientation is in degrees. ``yaw`` is rotation about the vertical axis,
    ``pitch`` is look up/down, ``roll`` is head tilt (usually 0 in FPS games).
    """

    position: Vec3
    yaw: float
    pitch: float = 0.0
    roll: float = 0.0


class SoundSource(_Strict):
    """The ground-truth sound emitter we want to reconstruct."""

    position: Vec3
    sound_type: str
    # Optional opaque id for an in-game entity / event that produced the sound.
    source_id: str | None = None


class SampleLabel(_Strict):
    """Ground-truth metadata for a single stereo capture.

    This is the schema for the ``--label xxx.json`` file.
    """

    schema_version: str = SCHEMA_VERSION
    map_id: str
    listener: ListenerPose
    source: SoundSource
    # Origin of the capture; validated against the project usage scope.
    capture_origin: str
    weapon: str | None = None
    game_build: str | None = None
    notes: str | None = None
    # Free-form bag for collector-specific metadata that we do not model yet.
    extra: dict[str, Any] = Field(default_factory=dict)


class AudioMeta(_Strict):
    """Probed, immutable facts about an imported audio file."""

    path: str  # path relative to the data root (portable across machines)
    format: str
    sample_rate: int
    channels: int
    num_frames: int
    duration_s: float
    sha256: str
    size_bytes: int


class SampleRecord(_Strict):
    """One line in ``manifest.jsonl``: audio + label + identity."""

    sample_id: str
    created_at: str  # ISO-8601 UTC timestamp
    audio: AudioMeta
    label: SampleLabel
    # Original label file path at import time, kept for provenance.
    label_source_path: str | None = None
