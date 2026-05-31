# Audio Features (Phase 2)

This page documents the feature set produced by
`core.features.extract_audio_features(audio_path, config)` and persisted by
`core.feature_store` as `data/features/<sample_id>.npz`.

Goal of this phase: **verify the audio can be expressed stably and meaningfully
for a machine.** No model is trained here.

## Pipeline

```
stereo file ─► load (soundfile, 2ch) ─► resample to features.sample_rate
            ─► binaural-safe stereo (none | global_peak)  ─► L, R  (ITD/ILD/diff)
            ─► mono = mean(L_raw, R_raw), optionally peak-normalized ─► spectral
```

A non-stereo file raises `FeatureExtractionError` — binaural cues (ITD/ILD) are
undefined for mono, so we fail loudly rather than degrade silently.

> ⚠️ **ILD is sensitive to normalization.** Normalizing the left and right
> channels *independently* (`per_channel_peak`) forces both to full scale and so
> **erases the inter-channel level difference** — ILD collapses to ~0 dB. The
> binaural cues therefore **never** use a per-channel-normalized signal: they use
> the raw resampled stereo or a `global_peak` (common-gain) version, which scales
> both channels by the same factor and preserves the L/R ratio. If
> `normalize_mode` is set to `per_channel_peak`, ITD/ILD silently fall back to
> `global_peak`; only the monaural spectral path uses the per-channel signal.

## Configuration (`features:` in `config.yaml`)

| Key | Default | Used for |
|-----|---------|----------|
| `sample_rate` | 48000 | Analysis rate; audio is resampled to this. |
| `n_fft` | 2048 | STFT window size. |
| `hop_length` | 512 | STFT hop. |
| `n_mels` | 128 | Mel band count. |
| `n_mfcc` | 40 | MFCC count. |
| `fmin` / `fmax` | 20 / 16000 | Mel frequency range (Hz). |
| `normalize_mode` | `global_peak` | Amplitude normalization (see below). |
| `itd_max_lag_ms` | 2.0 | Max inter-channel lag searched for ITD. |

### `normalize_mode`

| Mode | Effect | ILD-safe? |
|------|--------|-----------|
| `none` | Use the raw resampled signal. | ✅ |
| `global_peak` (default) | Divide **both** channels by their shared peak. Preserves the L/R ratio. | ✅ |
| `per_channel_peak` | Normalize each channel independently. **Destroys ILD.** | ❌ (never used for ITD/ILD) |

## Output keys

### Monaural spectral descriptors (computed on the channel mean)

| Key | Shape | Meaning |
|-----|-------|---------|
| `log_mel_spectrogram` | `(n_mels, frames)` | Power mel spectrogram in dB. |
| `mfcc` | `(n_mfcc, frames)` | Cepstral timbre coefficients. |
| `rms_energy` | `(frames,)` | Short-time loudness. |
| `zero_crossing_rate` | `(frames,)` | Noisiness / pitch proxy. |
| `spectral_centroid` | `(frames,)` | Center of mass of the spectrum (Hz). |
| `spectral_bandwidth` | `(frames,)` | Spectral spread (Hz). |
| `spectral_flux` | `(frames,)` | Half-wave-rectified frame-to-frame change. |

### Binaural cues (computed on L and R)

| Key | Shape | Meaning |
|-----|-------|---------|
| `stereo_channel_diff` | `(samples,)` | `L - R` waveform. |
| `itd_estimate_ms` | scalar | Inter-channel time difference (ms). |
| `ild_db` | scalar | Inter-channel level difference (dB). |

### Provenance

| Key | Type | Meaning |
|-----|------|---------|
| `duration_sec` | float | Clip duration at analysis rate. |
| `sample_rate` | int | Analysis sample rate. |
| `channels` | int | Source channel count (always 2 here). |

## ITD and ILD: what they mean

Direction is carried almost entirely by the *difference* between the two
channels:

- **ITD** (time): the near ear hears the sound first. Estimated by
  cross-correlating L and R within `±itd_max_lag_ms` and converting the
  best-aligning lag to milliseconds. **Sign:** positive ⇒ right channel delayed
  ⇒ source on the listener's **left**. Dominant cue at low frequencies.
- **ILD** (level): the head shadows the far ear. Computed as
  `10·log10(E_L / E_R)`. **Sign:** positive ⇒ left louder ⇒ source on the
  **left**. Dominant cue at high frequencies.

Together ITD + ILD give the left/right bearing of the source relative to the
listener's facing direction. Combined with the listener pose stored in the
label, this is the raw directional signal later phases will learn to map onto a
position in the map's point library.

### Capture guidance (important for ILD)

ILD is only meaningful if amplitude is consistent across captures. For
spatial-localization training:

- **Keep the system volume, in-game volume and sound-card / interface gain
  fixed** across the whole collection session. Changing any of them rescales a
  channel and corrupts ILD comparisons between samples.
- **Do not normalize the left and right channels separately** at any point in
  your capture/export chain. If you must normalize, use a common (global) gain
  so the L/R ratio is preserved (this is exactly what `normalize_mode:
  global_peak` does internally).
- Prefer a single, calibrated capture path so absolute levels are comparable.

## Storage format

`np.savez_compressed` into `data/features/<sample_id>.npz`. Scalars are stored
as 0-d arrays and restored to Python `float`/`int` by
`feature_store.load_features`, which returns the same dict shape that was saved.
`load_features` accepts either a path to a `.npz` or a `sample_id` plus a
`PathsConfig`.
