"""Core library for the FPS spatial-audio source-localization data backbone.

Phase 1 scope: configuration, data schema, manifest I/O, audio import and
label validation. No models, no realtime inference. The public surface is
re-exported here so callers can ``from core import ...`` without reaching into
submodules.
"""

from core.config import (
    AppConfig,
    AudioConfig,
    ImportConfig,
    LabelsConfig,
    PathsConfig,
    ProjectConfig,
    load_config,
)
from core.errors import (
    AudioImportError,
    ConfigError,
    DuplicateSampleError,
    LabelValidationError,
    LocatorError,
    ManifestError,
    SchemaValidationError,
)
from core.importer import import_sample
from core.manifest import ManifestStore
from core.schema import (
    AudioMeta,
    ListenerPose,
    SampleLabel,
    SampleRecord,
    SoundSource,
    Vec3,
)

__all__ = [
    "AppConfig",
    "AudioConfig",
    "ImportConfig",
    "LabelsConfig",
    "PathsConfig",
    "ProjectConfig",
    "load_config",
    "LocatorError",
    "ConfigError",
    "SchemaValidationError",
    "LabelValidationError",
    "AudioImportError",
    "DuplicateSampleError",
    "ManifestError",
    "import_sample",
    "ManifestStore",
    "AudioMeta",
    "ListenerPose",
    "SampleLabel",
    "SampleRecord",
    "SoundSource",
    "Vec3",
]
