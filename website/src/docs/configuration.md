---
title: Configuration
order: 4
intro: "Behaviour is driven by config/default.yaml. Override the keys you need; everything else falls back to sensible defaults."
---

Configuration lives in `config/default.yaml`. The main sections:

## Detectors

```yaml
detectors:
  yolo:
    model: yolo11s.pt      # detector tier (n / s / larger)
    imgsz: 1280            # inference resolution
    confidence: 0.25       # default confidence floor
    person_confidence: 0.18 # lower floor for people (awkward poses)
    frame_interval: 2      # run every Nth frame
  motion:
    enabled: true
    min_area: 500
```

## Spatial (depth + 3D)

```yaml
spatial:
  enabled: true
  input_width: 640         # depth working resolution
  fuse_frames: 3           # temporal median fusion
  fov_deg: 60              # assumed camera field of view
  complete: true           # reconstruct occluded background
```

## Reconstruction (super-resolution)

```yaml
reconstruct:
  super_resolution: true
  min_frames: 2
  max_frames: 16
  min_corr: 0.72           # reject mismatched crops
```

## Identity (roster / Re-ID)

```yaml
roster:
  persist: true
  persist_threshold: 0.74
gait:
  enabled: true
```

## Runtime toggles

Some settings are also editable live from the UI and persisted in the SQLite `settings` table, for example the DETECTION class filters (see the [API]({{ '/api/' | url }})). Live changes win over the file until reset.

<div class="callout"><div class="c-title">Per-model keys</div><p>Every model page lists the exact config keys that govern it, under <a href="{{ '/models/' | url }}">Models</a>.</p></div>
