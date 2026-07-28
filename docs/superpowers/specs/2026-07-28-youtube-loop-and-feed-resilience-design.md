# Downloaded Looping YouTube Sources + Feed Resilience — Design

**Date:** 2026-07-28
**Status:** Approved, auto-mode implementation

## Problem

Two connected issues:

1. **Hard to test with live data.** The operator wants to use a YouTube video as a
   camera. Today a YouTube URL is treated as a *live* source: `resolve_stream()` (yt-dlp)
   returns a signed HLS URL that `RtspReader`/`Cv2ThumbWorker` open via FFMPEG. The HLS URL
   is re-resolved on every entry and streamed live each time.

2. **All camera feeds lose signal on the map after a while.** The map (`Topology.svelte`)
   polls `/snap/{id}` per camera; a failed image load shows the animated NO SIGNAL overlay.
   Root cause: signed YouTube HLS URLs expire (and re-resolve can hit YouTube's bot gate),
   so both the active `RtspReader` and the preview `Cv2ThumbWorker` stop producing frames.
   Because the operator tests primarily with YouTube sources, *every* camera drops at once.
   Secondary contributors: `ThumbHub` caps at `max_workers=16` (LRU eviction) and reaps
   idle workers after 120 s.

## Goals

- Add a YouTube video as a source that behaves like a continuous live camera: **downloaded
  once** at up to 1080p, played **start to finish on a loop** (restarts when the app opens,
  wraps to the start on end). Image processing runs off it exactly like a live camera.
- **Never re-fetch** the same video from YouTube; keep it on disk and reuse it.
- **Feed resilience:** all camera previews stay live on the map; a camera that has produced
  a frame never falls back to NO SIGNAL. Heavy analysis stays on the single active camera
  (confirmed scope — previews stay alive, not full parallel analysis).

Non-goals: parallel full analysis of every camera; audio playback; per-camera calibration.

## Approach

A YouTube URL is resolved to a **local looping video file** at the source level, so
everything downstream (analysis reader + preview worker) just sees a stable local path that
never expires. This single change fixes both the feature and the signal-drop.

### Components

```
server/media.py        MediaLibrary — download-once cache of yt-dlp videos (<=1080p),
                       per-url state (downloading%/ready/failed), keyed by video id
camera/file_reader.py  FileLoopReader — plays a local video into the FrameBuffer at its
                       native FPS, seamlessly looping on EOF (RtspReader-compatible)
server/thumbs.py       FileThumbWorker — low-fps looping preview from a local file
server/backend.py      connect() routes yt-dlp sources through MediaLibrary+FileLoopReader;
                       ThumbHub previews use the local file; sources_payload carries
                       download state; /snap never hard-fails once a frame has been seen
web/.../LiveThumb+card DOWNLOADING state while a source is being fetched
```

### MediaLibrary (`server/media.py`)

- Stores videos under `data/media/<video_id>.mp4`, keyed by the yt-dlp video id so the same
  video is downloaded only once (across sessions).
- `local_path(url) -> Path | None`: returns the cached file if present; otherwise starts a
  **background** download and returns None until it is ready. Non-yt-dlp URLs return None.
- `state(url) -> {status: 'downloading'|'ready'|'failed', progress: float}` for the UI.
- Download via the yt-dlp Python API, format preferring H.264/mp4 video-only at ≤1080p:
  `bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]`. Video-only means no audio
  and **no ffmpeg merge step** (cv2 opens the single mp4 directly). Reuses `ytstream`'s
  cookie / player-client fallbacks to clear the bot gate.
- One in-flight download per video id (dedup); a `progress_hook` updates the percentage.

### FileLoopReader (`camera/file_reader.py`)

- Same interface as `RtspReader` (thread, `run`/`stop`, `on_status`, feeds `FrameBuffer`).
- Opens the local file with `cv2.VideoCapture`; reads sequentially and **paces to the
  video's native FPS** (sleep to the next frame's due time) so it plays at natural speed and
  looks like a real-time feed — not decoded as fast as possible.
- On EOF (`read()` returns False), **seeks to frame 0** (`CAP_PROP_POS_FRAMES = 0`) and
  continues → seamless loop. Reopens the capture on hard errors.
- Emits `connected` once frames flow; frame `timestamp` is wall-clock `time.time()` so the
  rest of the pipeline (analysis, clips) is unaffected.

### FileThumbWorker (`server/thumbs.py`)

- A `Cv2ThumbWorker` variant that opens a **local file** (not `resolve_stream`), loops on
  EOF, and emits a downscaled ~3 fps preview JPEG. Because the file never expires, the
  preview never gaps. `ThumbHub` picks this worker when the source resolves to a local file.

### Backend wiring (`server/backend.py`)

- `connect(source)`: if `is_stream_url(source.url)` (yt-dlp), ask `MediaLibrary.local_path`.
  - ready → `FileLoopReader(local_path, ...)`.
  - downloading → set conn `connecting`, surface the download state; a light waiter starts
    playback when the file becomes ready (no live HLS fallback).
  - failed → offline + the existing "YouTube blocked" guidance alert.
  RTSP/RTMP and MJPEG paths are unchanged.
- `ThumbHub.get_jpeg`: for a yt-dlp source, use the local media path + `FileThumbWorker`;
  if still downloading, return the persisted last frame (or a "downloading" placeholder is
  handled client-side via source state).
- `sources_payload()` adds `download` (`{status, progress}`) per source so the map/POV can
  show DOWNLOADING.
- `/snap` (`thumb_jpeg`): once a camera has produced any frame it is remembered and
  persisted; return that frozen frame instead of 503 so the map never flips to NO SIGNAL
  for a camera that has ever worked.
- `ThumbHub.max_workers` becomes configurable (`thumbs.max_workers`, default raised to cover
  a typical camera count) so map previews are not LRU-evicted.

### Frontend (minimal)

- `Camera` type gains an optional `download?: {status, progress}`. `LiveThumb`/card shows a
  small "DOWNLOADING n%" state (instead of NO SIGNAL) while a source is being fetched.

## Testing

- **MediaLibrary:** cache hit returns the existing file with no second download; format
  string is as specified; state transitions downloading→ready and →failed; concurrent calls
  for the same id dedupe to one download. Tests inject a fake downloader (no network).
- **FileLoopReader:** with a tiny generated mp4 (written via cv2.VideoWriter in the test),
  it feeds frames, wraps past the last frame back to the first (loop), paces roughly to FPS,
  and `stop()` ends the thread. No network.
- **FileThumbWorker:** produces a JPEG from a local file and keeps producing across the loop
  boundary.
- **/snap resilience:** after a camera has one remembered frame, `thumb_jpeg` returns it
  even when the live worker has no current frame.
- Full suite (`uv run pytest`) and `npm run build` stay green.

## Rollout order

1. `MediaLibrary` + tests.
2. `FileLoopReader` + tests.
3. `FileThumbWorker` + `ThumbHub` integration + tests.
4. Backend wiring (connect, thumb_jpeg hardening, sources_payload download state, config).
5. Frontend DOWNLOADING state.
6. Verify: pytest + npm build; manual smoke with a real YouTube URL.
