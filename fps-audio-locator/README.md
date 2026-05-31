# fps-audio-locator

**FPS 空间音频点位反演系统 — 数据工程底座 (Phase 1)**

A research system that investigates whether a sound **source position** can be
reconstructed from **stereo audio + listener pose + a map's point library**,
inside real game audio environments.

> ⚠️ **Compliance boundary.** This system is for **官方游戏自建房 / 训练房 /
> 离线录像 / 研发验证** only. It does **not** connect to real matchmaking,
> give realtime in-match hints, inject into the game client, or read game
> memory. Every imported sample's `capture_origin` is validated against the
> configured `usage_scope`. See [`docs/architecture.md`](docs/architecture.md).

This repository currently contains **Phase 1** only: a clean, tested data
engineering base. No model, no realtime inference, no second screen, no LED.

## What's here

| Area | File |
|------|------|
| Unified config | `configs/config.yaml` → `core/config.py` |
| Data schema | `core/schema.py` (see `docs/data_schema.md`) |
| Manifest I/O (`manifest.jsonl`) | `core/manifest.py` |
| Audio import | `core/audio_io.py` |
| Label validation | `core/labels.py` |
| Import pipeline | `core/importer.py` |
| CLI | `apps/cli.py` |
| Tests | `tests/` |

## Install

Python **3.11+** required (libsndfile is needed by `soundfile`).

```bash
cd fps-audio-locator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # Phase-1 base + pytest
# or: pip install -e .[dev]
```

> All commands below are run from inside the `fps-audio-locator/` directory so
> that `python -m apps.cli` and `import core` resolve correctly.

## Import your first sample

You need two files:

1. **A stereo audio clip** (`.wav` or `.flac`, 2 channels, 44.1/48 kHz).
2. **A label JSON** describing the ground truth — see
   [`docs/sample_label.example.json`](docs/sample_label.example.json) and
   [`docs/data_schema.md`](docs/data_schema.md).

A minimal label:

```json
{
  "map_id": "de_dust2",
  "capture_origin": "selfhost",
  "listener": { "position": { "x": 250, "y": -120, "z": 64 }, "yaw": 90 },
  "source":   { "position": { "x": 980, "y": 540, "z": 64 }, "sound_type": "footstep" }
}
```

Then import it:

```bash
python -m apps.cli import-sample --audio path/to/clip.wav --label path/to/label.json
```

On success you'll see the new `sample_id`, and a line is appended to
`data/manifest/manifest.jsonl`. The audio is copied into `data/raw/` and the
label is archived in `data/labels/`.

### No recording handy? Generate a demo

```bash
python scripts/make_demo_sample.py            # writes _demo/demo_sample.{wav,json}
python -m apps.cli import-sample --audio _demo/demo_sample.wav --label _demo/demo_sample.json
```

### Other commands

```bash
python -m apps.cli validate-label --label path/to/label.json   # check without importing
python -m apps.cli list-samples                                # list manifest contents
python -m apps.cli stats                                       # counts by map / sound type
```

All commands accept `--config PATH` (defaults to `configs/config.yaml`).

## What gets validated

**Audio:** must be stereo (`required_channels`), an allowed sample rate, an
allowed format, and within the duration range.

**Label (structural):** must match the schema in `core/schema.py`; unknown
fields are rejected.

**Label (semantic):** `map_id` ∈ `allowed_maps`, `sound_type` ∈
`allowed_sound_types`, `capture_origin` ∈ `usage_scope`, and listener/source
positions inside the map's bounding box.

**Duplicates:** detected by audio SHA-256; behavior controlled by
`import.on_duplicate` (`error` / `skip` / `overwrite`).

## Run the tests

```bash
pytest
```

The suite covers the data schema, manifest read/write round-trips, label field
validation, audio constraint checks and the full import pipeline.

## Configuration

Everything is driven by [`configs/config.yaml`](configs/config.yaml): data
paths, audio constraints, allowed maps/sound types, map bounds, usage scope and
import policy. Relative paths resolve against the project root.

## Roadmap

Phase 2 (feature extraction + map point library), Phase 3 (model training,
inference, error evaluation) and Phase 4 (debug console) are planned — see
[`docs/architecture.md`](docs/architecture.md).
