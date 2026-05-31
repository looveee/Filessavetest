"""Tests for diagnostic plotting and the Phase-2 CLI commands."""

from __future__ import annotations

import pytest

from apps.cli import main as cli_main
from core.diagnostics import PLOT_NAMES, generate_diagnostics
from core.errors import FeatureExtractionError
from core.feature_store import feature_path
from core.importer import import_sample


def _import_demo(config, make_wav, write_label, valid_label_dict):
    wav = make_wav(channels=2)
    label = write_label(valid_label_dict)
    return import_sample(wav, label, config)


def test_generate_diagnostics_writes_pngs(config, make_wav):
    wav = make_wav(channels=2)
    paths = generate_diagnostics("plot_sample", wav, config)
    names = {p.name for p in paths}
    assert names == set(PLOT_NAMES)
    for p in paths:
        assert p.is_file()
        assert p.stat().st_size > 0
        assert p.parent == config.paths.diagnostics_dir / "plot_sample"


def test_cli_extract_features_single(config_path, config, make_wav, write_label, valid_label_dict):
    record = _import_demo(config, make_wav, write_label, valid_label_dict)
    rc = cli_main(
        ["extract-features", "--sample-id", record.sample_id, "--config", str(config_path)]
    )
    assert rc == 0
    assert feature_path(record.sample_id, config.paths).is_file()


def test_cli_extract_features_all(config_path, config, make_wav, write_label, valid_label_dict):
    record = _import_demo(config, make_wav, write_label, valid_label_dict)
    rc = cli_main(["extract-features", "--all", "--config", str(config_path)])
    assert rc == 0
    assert feature_path(record.sample_id, config.paths).is_file()


def test_cli_plot_audio(config_path, config, make_wav, write_label, valid_label_dict, capsys):
    record = _import_demo(config, make_wav, write_label, valid_label_dict)
    rc = cli_main(
        ["plot-audio", "--sample-id", record.sample_id, "--config", str(config_path)]
    )
    assert rc == 0
    out_dir = config.paths.diagnostics_dir / record.sample_id
    for name in PLOT_NAMES:
        assert (out_dir / name).is_file()


def test_cli_extract_unknown_sample_errors(config_path, capsys):
    rc = cli_main(
        ["extract-features", "--sample-id", "nope", "--config", str(config_path)]
    )
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_extract_and_store_unknown_sample_raises(config):
    from core.feature_pipeline import extract_and_store

    with pytest.raises(FeatureExtractionError):
        extract_and_store("missing", config)
