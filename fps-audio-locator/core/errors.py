"""Typed exception hierarchy for the locator data backbone.

Every error raised intentionally by ``core`` derives from :class:`LocatorError`
so callers (CLI, future API) can catch one base type and render a clean message
instead of a stack trace.
"""

from __future__ import annotations


class LocatorError(Exception):
    """Base class for all errors raised by the locator core."""


class ConfigError(LocatorError):
    """Raised when the configuration file is missing, malformed or invalid."""


class SchemaValidationError(LocatorError):
    """Raised when a payload does not conform to the data schema."""


class LabelValidationError(LocatorError):
    """Raised when a label is structurally valid but violates config rules.

    ``issues`` carries the individual human-readable problems so the CLI can
    print them as a list.
    """

    def __init__(self, message: str, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues: list[str] = issues or []


class AudioImportError(LocatorError):
    """Raised when an audio file cannot be probed or fails its constraints."""


class DuplicateSampleError(LocatorError):
    """Raised when a sample already exists in the manifest."""


class ManifestError(LocatorError):
    """Raised when the manifest cannot be read or written."""
