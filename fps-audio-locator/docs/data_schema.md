# Data Schema (v1.0)

This document describes the data contract for a single sample. The schema is
enforced in code by the pydantic models in `core/schema.py`; this page is the
human-readable reference.

A **sample** is one stereo audio capture plus its ground-truth metadata. The
goal of the system is, given the stereo audio + listener pose + a map's point
library, to reconstruct the **source position**.

## Two layers

| Layer | Produced by | Stored as | Model |
|-------|-------------|-----------|-------|
| Label | Data collector | `xxx.json` (one per capture) | `SampleLabel` |
| Manifest record | Import pipeline | one line in `manifest.jsonl` | `SampleRecord` |

## Label (`SampleLabel`)

The JSON file passed to `--label`. Unknown fields are **rejected**.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `schema_version` | string | no (default `"1.0"`) | Schema version. |
| `map_id` | string | yes | Must be in `labels.allowed_maps`. |
| `capture_origin` | string | yes | Must be in `project.usage_scope` (compliance). |
| `listener` | `ListenerPose` | yes | Where the local player is/faces. |
| `source` | `SoundSource` | yes | Ground-truth emitter. |
| `weapon` | string\|null | no | Weapon involved, if any. |
| `game_build` | string\|null | no | Game build/version string. |
| `notes` | string\|null | no | Free text. |
| `extra` | object | no | Collector-specific metadata bag. |

### `ListenerPose`

| Field | Type | Notes |
|-------|------|-------|
| `position` | `Vec3` | Listener position. |
| `yaw` | float | Degrees, rotation about vertical axis. |
| `pitch` | float | Degrees, look up/down (default 0). |
| `roll` | float | Degrees, head tilt (default 0). |

### `SoundSource`

| Field | Type | Notes |
|-------|------|-------|
| `position` | `Vec3` | Source position (the prediction target). |
| `sound_type` | string | Must be in `labels.allowed_sound_types`. |
| `source_id` | string\|null | Optional in-game entity/event id. |

### `Vec3`

`{ "x": float, "y": float, "z": float }` in the configured coordinate system
(`labels.coordinate_system`, game units).

## Manifest record (`SampleRecord`)

Generated automatically; never hand-authored.

| Field | Type | Notes |
|-------|------|-------|
| `sample_id` | string | `<map_id>_<sound_type>_<sha8>` (derived from audio hash). |
| `created_at` | string | ISO-8601 UTC import timestamp. |
| `audio` | `AudioMeta` | Probed audio facts. |
| `label` | `SampleLabel` | The validated label. |
| `label_source_path` | string\|null | Original label path (provenance). |

### `AudioMeta`

| Field | Type | Notes |
|-------|------|-------|
| `path` | string | Path relative to `data_root` (portable). |
| `format` | string | e.g. `wav`. |
| `sample_rate` | int | Must be in `audio.allowed_sample_rates`. |
| `channels` | int | Must equal `audio.required_channels` (2). |
| `num_frames` | int | Total sample frames. |
| `duration_s` | float | Within `[min_duration_s, max_duration_s]`. |
| `sha256` | string | Content hash (identity + duplicate detection). |
| `size_bytes` | int | File size. |

See `docs/sample_label.example.json` for a complete example.
