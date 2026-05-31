"""Core library for the FPS spatial-audio source-localization data backbone.

Phase 1 scope: configuration, data schema, manifest I/O, audio import and
label validation. No models, no realtime inference. The public surface is
re-exported here so callers can ``from core import ...`` without reaching into
submodules.
"""

from core.config import (
    AppConfig,
    AudioConfig,
    FeaturesConfig,
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
    FeatureExtractionError,
    LabelValidationError,
    LocatorError,
    ManifestError,
    SchemaValidationError,
)
from core.features import FEATURE_KEYS, extract_audio_features
from core.feature_store import (
    feature_path,
    load_features,
    save_features,
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
    "FeaturesConfig",
    "ImportConfig",
    "LabelsConfig",
    "PathsConfig",
    "ProjectConfig",
    "load_config",
    "FEATURE_KEYS",
    "extract_audio_features",
    "feature_path",
    "load_features",
    "save_features",
    "LocatorError",
    "ConfigError",
    "SchemaValidationError",
    "LabelValidationError",
    "AudioImportError",
    "DuplicateSampleError",
    "ManifestError",
    "FeatureExtractionError",
    "import_sample",
    "ManifestStore",
    "AudioMeta",
    "ListenerPose",
    "SampleLabel",
    "SampleRecord",
    "SoundSource",
    "Vec3",
]
