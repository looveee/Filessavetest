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
| **1** | Data engineering base: config, schema, manifest, import, label validation, CLI, tests | ✅ this repo |
| 2 | Feature extraction (resample, STFT/ITD/ILD), map point library | planned |
| 3 | Model training (PyTorch), inference, error evaluation | planned |
| 4 | Debug console (FastAPI + Streamlit/React), parameter tuning | planned |

Phase 1 deliberately ships **no model, no realtime inference, no second
screen, no LED**. The point is a solid data backbone first.

## Module layout

```
fps-audio-locator/
├─ apps/            # entry points
│  └─ cli.py        # `python -m apps.cli ...`
├─ core/            # library
│  ├─ config.py     # config.yaml -> typed AppConfig
│  ├─ schema.py     # SampleLabel / SampleRecord / AudioMeta ...
│  ├─ labels.py     # load + structural + semantic validation
│  ├─ audio_io.py   # probe / validate / import audio (headers only in P1)
│  ├─ manifest.py   # append-only manifest.jsonl store
│  ├─ importer.py   # orchestration: validate -> dedupe -> persist
│  └─ errors.py     # typed exception hierarchy
├─ configs/         # config.yaml (single source of truth)
├─ data/            # raw/ (audio), labels/ (archived json), manifest/
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
