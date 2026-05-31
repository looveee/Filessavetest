"""Unified configuration system.

The single source of truth is ``configs/config.yaml``. It is parsed into a tree
of pydantic models so that every consumer works against typed, validated
objects instead of raw dictionaries. Relative paths in the file are resolved
against the *project root* (the directory that contains ``configs/``) at load
time, so the rest of the codebase only ever sees absolute paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.errors import ConfigError

# Default config location relative to the project root.
DEFAULT_CONFIG_RELPATH = Path("configs/config.yaml")


class _Strict(BaseModel):
    """Base model that rejects unknown keys to catch config typos early."""

    model_config = ConfigDict(extra="forbid")


class ProjectConfig(_Strict):
    name: str
    version: str
    # Compliance boundary: the only contexts this system is built for.
    usage_scope: list[str] = Field(min_length=1)


class PathsConfig(_Strict):
    data_root: Path
    audio_dir: Path
    label_dir: Path
    manifest_path: Path
    features_dir: Path
    diagnostics_dir: Path

    def resolve(self, base_dir: Path) -> "PathsConfig":
        """Return a copy with every relative path anchored at ``base_dir``."""

        def anchor(p: Path) -> Path:
            p = Path(p)
            return p if p.is_absolute() else (base_dir / p)

        return PathsConfig(
            data_root=anchor(self.data_root),
            audio_dir=anchor(self.audio_dir),
            label_dir=anchor(self.label_dir),
            manifest_path=anchor(self.manifest_path),
            features_dir=anchor(self.features_dir),
            diagnostics_dir=anchor(self.diagnostics_dir),
        )


class AudioConfig(_Strict):
    required_channels: int = Field(ge=1)
    allowed_sample_rates: list[int] = Field(min_length=1)
    resample_target: int = Field(gt=0)
    min_duration_s: float = Field(ge=0)
    max_duration_s: float = Field(gt=0)
    allowed_formats: list[str] = Field(min_length=1)

    @field_validator("allowed_formats")
    @classmethod
    def _normalize_formats(cls, v: list[str]) -> list[str]:
        return [fmt.lower().lstrip(".") for fmt in v]


class FeaturesConfig(_Strict):
    """STFT / mel / MFCC parameters and ITD search window for Phase 2."""

    sample_rate: int = Field(gt=0)
    n_fft: int = Field(gt=0)
    hop_length: int = Field(gt=0)
    n_mels: int = Field(gt=0)
    n_mfcc: int = Field(gt=0)
    fmin: float = Field(ge=0)
    fmax: float = Field(gt=0)
    # How to normalize amplitude before analysis:
    #   none              -> no normalization (raw resampled signal)
    #   global_peak       -> divide BOTH channels by the shared peak (default);
    #                        preserves the inter-channel level ratio -> ILD safe
    #   per_channel_peak  -> normalize each channel independently; DESTROYS the
    #                        inter-channel level difference. NEVER used for
    #                        ITD/ILD (binaural cues fall back to global_peak).
    normalize_mode: Literal["none", "global_peak", "per_channel_peak"] = "global_peak"
    itd_max_lag_ms: float = Field(gt=0)


class BoundingBox(_Strict):
    """Axis-aligned bounds used as a sanity check for point positions."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]

    def contains(self, point: tuple[float, float, float]) -> bool:
        return all(self.min[i] <= point[i] <= self.max[i] for i in range(3))


class LabelsConfig(_Strict):
    coordinate_system: str
    allowed_maps: list[str] = Field(min_length=1)
    allowed_sound_types: list[str] = Field(min_length=1)
    map_bounds: dict[str, BoundingBox] = Field(default_factory=dict)


class ImportConfig(_Strict):
    copy_audio: bool = True
    on_duplicate: Literal["error", "skip", "overwrite"] = "error"


class AppConfig(_Strict):
    """Root configuration object."""

    project: ProjectConfig
    paths: PathsConfig
    audio: AudioConfig
    features: FeaturesConfig
    labels: LabelsConfig
    # ``import`` is a Python keyword, so the YAML key is mapped via an alias.
    import_: ImportConfig = Field(alias="import")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _project_root_from_config(config_path: Path) -> Path:
    """Project root is the parent of the ``configs/`` directory."""
    return config_path.resolve().parent.parent


def load_config(
    config_path: str | Path | None = None,
    *,
    base_dir: str | Path | None = None,
) -> AppConfig:
    """Load, validate and path-resolve the configuration.

    Args:
        config_path: Path to ``config.yaml``. If ``None``, defaults to
            ``<base_dir or cwd>/configs/config.yaml``.
        base_dir: Project root used to anchor relative paths. If ``None`` it is
            inferred as the parent of the config file's directory.

    Raises:
        ConfigError: if the file is missing or fails validation.
    """
    if config_path is None:
        root = Path(base_dir) if base_dir else Path.cwd()
        config_path = root / DEFAULT_CONFIG_RELPATH
    config_path = Path(config_path)

    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"Failed to parse YAML config {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(raw).__name__}")

    try:
        cfg = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {config_path}:\n{exc}") from exc

    root = Path(base_dir) if base_dir else _project_root_from_config(config_path)
    cfg.paths = cfg.paths.resolve(root)
    return cfg
