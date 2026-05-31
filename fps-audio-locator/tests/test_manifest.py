"""Tests for manifest.jsonl read/write (core/manifest.py)."""

from __future__ import annotations

import pytest

from core.errors import ManifestError
from core.manifest import ManifestStore
from core.schema import AudioMeta, SampleLabel, SampleRecord


def _make_record(valid_label_dict, *, sample_id="s1", sha="a" * 64) -> SampleRecord:
    return SampleRecord(
        sample_id=sample_id,
        created_at="2026-05-31T00:00:00+00:00",
        audio=AudioMeta(
            path="raw/s1.wav",
            format="wav",
            sample_rate=48000,
            channels=2,
            num_frames=48000,
            duration_s=1.0,
            sha256=sha,
            size_bytes=192044,
        ),
        label=SampleLabel.model_validate(valid_label_dict),
    )


def test_append_and_read_roundtrip(tmp_path, valid_label_dict):
    store = ManifestStore(tmp_path / "manifest" / "manifest.jsonl")
    rec = _make_record(valid_label_dict)
    store.append(rec)

    # File and parent dir created on demand.
    assert store.exists()
    records = store.read_all()
    assert len(records) == 1
    assert records[0] == rec


def test_append_many_preserves_order(tmp_path, valid_label_dict):
    store = ManifestStore(tmp_path / "manifest.jsonl")
    recs = [
        _make_record(valid_label_dict, sample_id=f"s{i}", sha=str(i) * 64)
        for i in range(5)
    ]
    store.append_many(recs)
    assert [r.sample_id for r in store.read_all()] == ["s0", "s1", "s2", "s3", "s4"]
    assert store.count() == 5


def test_read_missing_file_is_empty(tmp_path):
    store = ManifestStore(tmp_path / "does_not_exist.jsonl")
    assert store.read_all() == []
    assert store.count() == 0
    assert store.sample_ids() == set()


def test_blank_lines_are_skipped(tmp_path, valid_label_dict):
    path = tmp_path / "manifest.jsonl"
    rec = _make_record(valid_label_dict)
    path.write_text(rec.model_dump_json() + "\n\n   \n", encoding="utf-8")
    store = ManifestStore(path)
    assert store.count() == 1


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text("{not valid json}\n", encoding="utf-8")
    store = ManifestStore(path)
    with pytest.raises(ManifestError):
        store.read_all()


def test_checksums_and_lookup(tmp_path, valid_label_dict):
    store = ManifestStore(tmp_path / "manifest.jsonl")
    rec = _make_record(valid_label_dict, sample_id="s1", sha="f" * 64)
    store.append(rec)
    assert store.checksums() == {"f" * 64: "s1"}
    assert store.find_by_checksum("f" * 64).sample_id == "s1"
    assert store.find_by_checksum("0" * 64) is None
    assert store.contains_sample_id("s1") is True
    assert store.contains_sample_id("nope") is False


def test_append_rejects_non_record(tmp_path):
    store = ManifestStore(tmp_path / "manifest.jsonl")
    with pytest.raises(ManifestError):
        store.append({"not": "a record"})  # type: ignore[arg-type]
