"""Read/write access to the sample manifest (``manifest.jsonl``).

The manifest is an append-only JSON Lines file: one :class:`SampleRecord` per
line. JSONL is chosen over a single JSON array so the file can grow
incrementally and stream-parse without loading everything into memory.

Appends are atomic-per-line and the writer creates parent directories on
demand. Reads are tolerant of blank lines (e.g. a trailing newline) but reject
malformed JSON so corruption is surfaced rather than silently skipped.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from core.errors import ManifestError
from core.schema import SampleRecord


class ManifestStore:
    """Thin persistence layer around a single ``manifest.jsonl`` file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- reading ---------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def iter_records(self) -> Iterator[SampleRecord]:
        """Yield records one at a time. Empty/whitespace lines are skipped."""
        if not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ManifestError(
                        f"Malformed JSON in {self.path}:{lineno}: {exc}"
                    ) from exc
                try:
                    yield SampleRecord.model_validate(payload)
                except ValidationError as exc:
                    raise ManifestError(
                        f"Invalid record in {self.path}:{lineno}:\n{exc}"
                    ) from exc

    def read_all(self) -> list[SampleRecord]:
        return list(self.iter_records())

    def count(self) -> int:
        return sum(1 for _ in self.iter_records())

    def sample_ids(self) -> set[str]:
        return {r.sample_id for r in self.iter_records()}

    def checksums(self) -> dict[str, str]:
        """Map of audio sha256 -> sample_id for duplicate detection."""
        return {r.audio.sha256: r.sample_id for r in self.iter_records()}

    def contains_sample_id(self, sample_id: str) -> bool:
        return any(r.sample_id == sample_id for r in self.iter_records())

    def get(self, sample_id: str) -> SampleRecord | None:
        for r in self.iter_records():
            if r.sample_id == sample_id:
                return r
        return None

    def find_by_checksum(self, sha256: str) -> SampleRecord | None:
        for r in self.iter_records():
            if r.audio.sha256 == sha256:
                return r
        return None

    # -- writing ---------------------------------------------------------

    def append(self, record: SampleRecord) -> None:
        """Append a single validated record as one JSON line."""
        if not isinstance(record, SampleRecord):
            raise ManifestError(
                f"append() expects a SampleRecord, got {type(record).__name__}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def append_many(self, records: list[SampleRecord]) -> None:
        for record in records:
            self.append(record)
