"""End-to-end import pipeline tests (core/importer.py) + CLI smoke test."""

from __future__ import annotations

import pytest

from apps.cli import main as cli_main
from core.errors import AudioImportError, DuplicateSampleError, LabelValidationError
from core.importer import import_sample, make_sample_id
from core.manifest import ManifestStore


def test_import_creates_manifest_entry(config, make_wav, write_label, valid_label_dict):
    wav = make_wav()
    label = write_label(valid_label_dict)
    record = import_sample(wav, label, config)

    assert record.sample_id.startswith("de_dust2_footstep_run_")
    # Manifest has exactly one entry.
    store = ManifestStore(config.paths.manifest_path)
    assert store.count() == 1
    # Audio copied into data/raw and label archived into data/labels.
    assert (config.paths.audio_dir / f"{record.sample_id}.wav").is_file()
    assert (config.paths.label_dir / f"{record.sample_id}.json").is_file()
    # Stored audio path is relative to data_root.
    assert record.audio.path == f"raw/{record.sample_id}.wav"


def test_sample_id_is_deterministic(valid_label_dict):
    from core.schema import SampleLabel

    label = SampleLabel.model_validate(valid_label_dict)
    sha = "abcd1234" + "0" * 56
    assert make_sample_id(label, sha) == "de_dust2_footstep_run_abcd1234"


def test_invalid_label_blocks_import(config, make_wav, write_label, valid_label_dict):
    valid_label_dict["map_id"] = "de_unknown"
    label = write_label(valid_label_dict)
    with pytest.raises(LabelValidationError):
        import_sample(make_wav(), label, config)
    # Nothing persisted.
    assert ManifestStore(config.paths.manifest_path).count() == 0


def test_invalid_audio_blocks_import(config, make_wav, write_label, valid_label_dict):
    label = write_label(valid_label_dict)
    with pytest.raises(AudioImportError):
        import_sample(make_wav(channels=1), label, config)
    assert ManifestStore(config.paths.manifest_path).count() == 0


def test_duplicate_default_errors(config, make_wav, write_label, valid_label_dict):
    wav = make_wav()
    label = write_label(valid_label_dict)
    import_sample(wav, label, config)
    with pytest.raises(DuplicateSampleError):
        import_sample(wav, label, config)
    assert ManifestStore(config.paths.manifest_path).count() == 1


def test_duplicate_skip_returns_existing(config, make_wav, write_label, valid_label_dict):
    config.import_.on_duplicate = "skip"
    wav = make_wav()
    label = write_label(valid_label_dict)
    first = import_sample(wav, label, config)
    second = import_sample(wav, label, config)
    assert first.sample_id == second.sample_id
    assert ManifestStore(config.paths.manifest_path).count() == 1


def test_cli_import_smoke(config_path, make_wav, write_label, valid_label_dict, capsys):
    wav = make_wav()
    label = write_label(valid_label_dict)
    rc = cli_main(
        [
            "import-sample",
            "--audio",
            str(wav),
            "--label",
            str(label),
            "--config",
            str(config_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Imported sample:" in out


def test_cli_validate_label_invalid(config_path, write_label, valid_label_dict):
    valid_label_dict["source"]["sound_type"] = "laser"
    label = write_label(valid_label_dict)
    rc = cli_main(
        ["validate-label", "--label", str(label), "--config", str(config_path)]
    )
    assert rc == 1
