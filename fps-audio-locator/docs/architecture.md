# Architecture

## Compliance boundary (read first)

This system exists **only** for:

- 正式游戏自建房 (official-game self-hosted rooms)
- 训练房 (training rooms)
- 离线录像 (offline replays)
- 研发验证 (R&D validation)

It does **not** and **will not**:

- connect to real matchmaking,
- give realtime in-match hints,
- inject into the game client,
- read game memory.

Every label carries a `capture_origin` that is validated against
`project.usage_scope`, so out-of-scope data is rejected at import time.

## Phase roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| **1** | Data engineering base: config, schema, manifest, import, label validation, CLI, tests | ✅ done |
| **2** | Feature extraction (resample, mel/MFCC/spectral + ITD/ILD), `.npz` store, diagnostic plots | ✅ done |
| 2b | Map point library | planned |
| 3 | Model training (PyTorch), inference, error evaluation | planned |
| 4 | Debug console (FastAPI + Streamlit/React), parameter tuning | planned |

Phases 1–2 deliberately ship **no model, no realtime inference, no second
screen, no LED**. Phase 2 only verifies the audio can be expressed stably and
meaningfully for a machine (the feature layer).

## Module layout

```
fps-audio-locator/
├─ apps/            # entry points
│  └─ cli.py        # `python -m apps.cli ...`
├─ core/            # library
│  ├─ config.py         # config.yaml -> typed AppConfig
│  ├─ schema.py         # SampleLabel / SampleRecord / AudioMeta ...
│  ├─ labels.py         # load + structural + semantic validation
│  ├─ audio_io.py       # probe / validate / import audio (headers only in P1)
│  ├─ manifest.py       # append-only manifest.jsonl store
│  ├─ importer.py       # import orchestration: validate -> dedupe -> persist
│  ├─ features.py       # P2: extract mel/MFCC/spectral + ITD/ILD
│  ├─ feature_store.py  # P2: save/load features as .npz
│  ├─ feature_pipeline.py # P2: manifest -> extract -> store orchestration
│  ├─ diagnostics.py    # P2: waveform/spectrogram/mel/stereo-diff plots
│  └─ errors.py         # typed exception hierarchy
├─ configs/         # config.yaml (single source of truth)
├─ data/            # raw/ labels/ manifest/ features/ diagnostics/
├─ docs/            # this folder
├─ tests/           # pytest suite
└─ scripts/         # helper/demo scripts
```

## Import data flow

```
--audio file ─┐
              ├─► import_sample()
--label json ─┘        │
                       ├─ load_label()        (structural, pydantic)
                       ├─ validate_label()    (semantic, vs config)
                       ├─ probe_audio()        (soundfile header read)
                       ├─ validate_probe()     (stereo / rate / duration)
                       ├─ duplicate check      (by sha256, on_duplicate policy)
                       ├─ import_audio()        (copy into data/raw/)
                       └─ manifest.append()     (one JSONL line)
```

The CLI is intentionally thin; all logic lives in `core` so the future API and
batch tools reuse the exact same import path.

## Feature extraction flow (Phase 2)

```
manifest record ─► resolve audio (data_root / audio.path)
                ─► load_analysis_audio()  (resample + normalize, stereo only)
                ─► extract_audio_features()
                     ├─ monaural: log-mel, MFCC, RMS, ZCR, centroid, bandwidth, flux
                     └─ binaural: stereo_diff, ITD (xcorr), ILD (energy ratio)
                ─► save_features()  ->  data/features/<sample_id>.npz

plot-audio ─► generate_diagnostics()  ->  data/diagnostics/<sample_id>/*.png
```

See [`features.md`](features.md) for the full feature catalog and the ITD/ILD
sign conventions.
