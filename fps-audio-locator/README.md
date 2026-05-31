# fps-audio-locator

**FPS 空间音频点位反演系统 — 数据工程底座 + 特征层 (Phase 1–2)**

A research system that investigates whether a sound **source position** can be
reconstructed from **stereo audio + listener pose + a map's point library**,
inside real game audio environments.

> ⚠️ **Compliance boundary.** This system is for **官方游戏自建房 / 训练房 /
> 离线录像 / 研发验证** only. It does **not** connect to real matchmaking,
> give realtime in-match hints, inject into the game client, or read game
> memory. Every imported sample's `capture_origin` is validated against the
> configured `usage_scope`. See [`docs/architecture.md`](docs/architecture.md).

This repository contains **Phase 1** (data engineering base) and **Phase 2**
(audio feature extraction + diagnostics). Still **no model, no realtime
inference, no second screen, no LED** — Phase 2 only answers "can the audio be
expressed stably and meaningfully for a machine?".

## What's here

| Area | File |
|------|------|
| Unified config | `configs/config.yaml` → `core/config.py` |
| Data schema | `core/schema.py` (see `docs/data_schema.md`) |
| Manifest I/O (`manifest.jsonl`) | `core/manifest.py` |
| Audio import | `core/audio_io.py` |
| Label validation | `core/labels.py` |
| Import pipeline | `core/importer.py` |
| **Feature extraction** | `core/features.py` (see `docs/features.md`) |
| **Feature store (`.npz`)** | `core/feature_store.py` |
| **Diagnostic plots** | `core/diagnostics.py` |
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
  "source":   { "position": { "x": 980, "y": 540, "z": 64 }, "sound_type": "footstep_run" }
}
```

> 💡 **Recommended for training.** The schema also accepts optional
> training-oriented fields — `listener_point_id`, `source_point_id`, `action`,
> `material`, `floor_relation`, `distance_m`, `occlusion`, `area_id`. They are
> not required to import, but you should fill them in during collection: they are
> the labels Phase 3 will train/evaluate against, and backfilling later is
> painful. See [`docs/sample_label.example.json`](docs/sample_label.example.json)
> and [`docs/data_schema.md`](docs/data_schema.md).

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

## Extract features (Phase 2)

Once a sample is in the manifest, turn its audio into a standard feature set:

```bash
python -m apps.cli extract-features --sample-id de_dust2_footstep_f367eaa1
python -m apps.cli extract-features --all          # every sample in the manifest
```

This writes `data/features/<sample_id>.npz`. Each archive holds:

| Feature | Shape | Meaning |
|---------|-------|---------|
| `log_mel_spectrogram` | `(n_mels, frames)` | Mel spectrogram in dB — the modeling front-end. |
| `mfcc` | `(n_mfcc, frames)` | Timbre summary. |
| `rms_energy` | `(frames,)` | Loudness envelope. |
| `zero_crossing_rate` | `(frames,)` | Noisiness / pitch proxy. |
| `spectral_centroid` | `(frames,)` | Spectral "brightness". |
| `spectral_bandwidth` | `(frames,)` | Spectral spread. |
| `spectral_flux` | `(frames,)` | Frame-to-frame change (onsets). |
| `stereo_channel_diff` | `(samples,)` | Left − Right waveform. |
| `itd_estimate_ms` | scalar | Inter-channel **time** difference (see below). |
| `ild_db` | scalar | Inter-channel **level** difference (see below). |
| `duration_sec`, `sample_rate`, `channels` | scalar | Provenance. |

All STFT/mel/MFCC parameters live under `features:` in `config.yaml`. Audio is
resampled to `features.sample_rate` and (optionally) peak-normalized per channel
first, so features are comparable across captures.

Load features back in Python:

```python
from core import load_config, load_features
cfg = load_config("configs/config.yaml")
feats = load_features("de_dust2_footstep_f367eaa1", cfg.paths)
print(feats["itd_estimate_ms"], feats["ild_db"], feats["log_mel_spectrogram"].shape)
```

## Diagnostic plots

```bash
python -m apps.cli plot-audio --sample-id de_dust2_footstep_f367eaa1
```

Writes four PNGs to `data/diagnostics/<sample_id>/`:

- `waveform.png` — left/right time-domain waveforms.
- `spectrogram.png` — linear-frequency STFT magnitude (dB).
- `mel.png` — log-mel spectrogram (the modeling front-end).
- `stereo_diff.png` — left−right difference, annotated with ITD/ILD.

Use these to eyeball whether a capture is sane before it ever feeds a model.

## Understanding ITD and ILD

These two **binaural cues** are how a listener (and our system) infers
direction from two ears / two channels:

- **ITD (Inter-channel Time Difference, ms)** — sound from the side reaches the
  near ear slightly *earlier* than the far ear. We estimate it via
  cross-correlation between the two channels, searching within
  `features.itd_max_lag_ms`. **Sign convention:** a **positive** ITD means the
  right channel is delayed (sound hits the left first) → the source is on the
  listener's **left**. ITD dominates direction perception at low frequencies.

- **ILD (Inter-channel Level Difference, dB)** — the head "shadows" the far ear,
  so the near channel is *louder*. We compute it as the energy ratio between
  channels: `10·log10(E_left / E_right)`. **Positive ILD ⇒ left louder ⇒ source
  on the left.** ILD dominates at high frequencies.

Together, ITD + ILD give the **left/right** bearing of the source relative to
the listener's facing direction; combined with the listener pose in the label,
that is the raw directional signal the later phases will learn to map to a map
position. The diagnostic `stereo_diff.png` shows both values in its title so you
can sanity-check that a known-direction capture has the expected sign.

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
validation, audio constraint checks, the full import pipeline, feature
extraction (including mono rejection + ITD/ILD sign), `.npz` save/load and the
diagnostic plot generation.

## Configuration

Everything is driven by [`configs/config.yaml`](configs/config.yaml): data
paths, audio constraints, allowed maps/sound types, map bounds, usage scope,
import policy and the `features:` block (STFT/mel/MFCC parameters + ITD search
window). Relative paths resolve against the project root.

## Roadmap

Phase 1 (data backbone) ✅ and Phase 2 (feature extraction + diagnostics) ✅ are
done. Phase 2b (map point library), Phase 3 (model training, inference, error
evaluation) and Phase 4 (debug console) are planned — see
[`docs/architecture.md`](docs/architecture.md).
