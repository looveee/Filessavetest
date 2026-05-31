"""Command-line entry point for the data backbone.

Usage:
    python -m apps.cli import-sample --audio xxx.wav --label xxx.json
    python -m apps.cli validate-label --label xxx.json
    python -m apps.cli list-samples
    python -m apps.cli stats

All commands accept ``--config PATH`` to point at an alternate config file
(defaults to ``configs/config.yaml`` relative to the current directory).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.config import load_config
from core.errors import LabelValidationError, LocatorError
from core.importer import import_sample
from core.labels import check_label, load_label
from core.manifest import ManifestStore


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: configs/config.yaml under cwd).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli",
        description="FPS spatial-audio source-localization data backbone (Phase 1).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser(
        "import-sample", help="Validate and import an (audio, label) pair."
    )
    p_import.add_argument("--audio", type=Path, required=True, help="Audio file path.")
    p_import.add_argument("--label", type=Path, required=True, help="Label JSON path.")
    _add_config_arg(p_import)
    p_import.set_defaults(func=_cmd_import_sample)

    p_validate = sub.add_parser(
        "validate-label", help="Validate a label JSON without importing."
    )
    p_validate.add_argument("--label", type=Path, required=True, help="Label JSON path.")
    _add_config_arg(p_validate)
    p_validate.set_defaults(func=_cmd_validate_label)

    p_list = sub.add_parser("list-samples", help="List sample ids in the manifest.")
    _add_config_arg(p_list)
    p_list.set_defaults(func=_cmd_list_samples)

    p_stats = sub.add_parser("stats", help="Show manifest summary statistics.")
    _add_config_arg(p_stats)
    p_stats.set_defaults(func=_cmd_stats)

    return parser


def _cmd_import_sample(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    record = import_sample(args.audio, args.label, config)
    print(f"Imported sample: {record.sample_id}")
    print(f"  map={record.label.map_id} sound={record.label.source.sound_type}")
    print(
        f"  audio={record.audio.path} "
        f"({record.audio.channels}ch @ {record.audio.sample_rate}Hz, "
        f"{record.audio.duration_s}s)"
    )
    print(f"  sha256={record.audio.sha256[:12]}…")
    print(f"  manifest={config.paths.manifest_path}")
    return 0


def _cmd_validate_label(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    label = load_label(args.label)  # structural validation
    issues = check_label(label, config)  # semantic validation
    if issues:
        print(f"INVALID: {args.label} ({len(issues)} issue(s)):", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    print(f"OK: {args.label} is valid.")
    return 0


def _cmd_list_samples(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = ManifestStore(config.paths.manifest_path)
    records = store.read_all()
    if not records:
        print("(manifest is empty)")
        return 0
    for r in records:
        print(
            f"{r.sample_id}\t{r.label.map_id}\t{r.label.source.sound_type}\t"
            f"{r.audio.duration_s}s\t{r.created_at}"
        )
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = ManifestStore(config.paths.manifest_path)
    records = store.read_all()
    print(f"manifest: {config.paths.manifest_path}")
    print(f"total samples: {len(records)}")
    by_map: dict[str, int] = {}
    by_sound: dict[str, int] = {}
    for r in records:
        by_map[r.label.map_id] = by_map.get(r.label.map_id, 0) + 1
        st = r.label.source.sound_type
        by_sound[st] = by_sound.get(st, 0) + 1
    if by_map:
        print("by map:")
        for k, v in sorted(by_map.items()):
            print(f"  {k}: {v}")
        print("by sound_type:")
        for k, v in sorted(by_sound.items()):
            print(f"  {k}: {v}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LabelValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    except LocatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
