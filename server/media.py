"""Download-once cache for yt-dlp video sources.

A YouTube URL becomes a local video file, downloaded a single time at up to 1080p and
reused forever (keyed by video id, so the same video is never fetched twice — even across
app restarts, and even if added via a different URL form). Downstream, the file is played
on a loop as if it were a continuous live camera, which also means it never expires the way
a signed HLS URL does. Downloads run in the background; per-url state drives the UI.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger("overseer.media")

# yt-dlp video id from the common YouTube URL shapes.
_YT_ID = re.compile(r"(?:v=|/live/|/embed/|/shorts/|youtu\.be/)([0-9A-Za-z_-]{11})")

# Prefer H.264/mp4 video-only at <= max height (no audio -> no ffmpeg merge; cv2 opens it
# directly), then any codec at that height, then a muxed fallback.
def _format_selector(max_h: int) -> str:
    return (f"bv*[height<={max_h}][ext=mp4]/bv*[height<={max_h}]/b[height<={max_h}]")


_VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}


def youtube_id(url: str) -> str | None:
    """The 11-char YouTube video id, or None if the URL isn't a recognizable YouTube URL."""
    m = _YT_ID.search(url or "")
    return m.group(1) if m else None


def source_key(url: str) -> str:
    """Stable cache key: the YouTube video id when present, else a short hash of the URL."""
    vid = youtube_id(url)
    if vid:
        return vid
    return "u" + hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


# downloader(url, out_dir, key, max_h, progress_cb) -> Path to the finished file.
Downloader = Callable[[str, Path, str, int, Callable[[float], None]], Path]


class MediaLibrary:
    def __init__(self, media_dir: Path, downloader: Downloader | None = None,
                 max_height: int = 1080, max_concurrent: int = 1) -> None:
        self._dir = Path(media_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_h = int(max_height)
        self._download = downloader or _ytdlp_download
        self._state: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        # Serialize downloads so navigating several cameras doesn't kick off a storm of
        # concurrent multi-hundred-MB fetches that saturate the box and starve the map's
        # preview workers (which is what made all thumbnails drop after a few connections).
        self._dl_sem = threading.Semaphore(max(1, int(max_concurrent)))

    def _cached_file(self, key: str) -> Path | None:
        # only real video files — never a .part in progress or a .poster.jpg image
        for p in sorted(self._dir.glob(f"{key}.*")):
            if p.suffix.lower() in _VIDEO_EXTS and p.is_file() and p.stat().st_size > 0:
                return p
        return None

    def cached_path(self, url: str) -> Path | None:
        """The downloaded local file if present, WITHOUT triggering a download. Used by
        previews so merely looking at the map never starts a multi-GB fetch."""
        return self._cached_file(source_key(url))

    def poster(self, url: str) -> bytes | None:
        """A still poster (the video's YouTube thumbnail) to show before/while the video
        downloads, so a YouTube camera has an image immediately instead of NO SIGNAL.
        Fetched once in the background and cached; None until it lands."""
        vid = youtube_id(url)
        if not vid:
            return None
        pf = self._dir / f"{vid}.poster.jpg"
        if pf.exists() and pf.stat().st_size > 0:
            return pf.read_bytes()
        with self._lock:
            key = f"poster:{vid}"
            th = self._threads.get(key)
            if th is None or not th.is_alive():
                th = threading.Thread(target=self._fetch_poster, args=(vid, pf),
                                      name=f"Poster({vid})", daemon=True)
                self._threads[key] = th
                th.start()
        return None

    @staticmethod
    def _fetch_poster(vid: str, dest: Path) -> None:
        import requests
        for quality in ("maxresdefault", "sddefault", "hqdefault"):
            try:
                r = requests.get(f"https://img.youtube.com/vi/{vid}/{quality}.jpg", timeout=6)
                if r.status_code == 200 and len(r.content) > 1500 and r.content[:2] == b"\xff\xd8":
                    dest.write_bytes(r.content)
                    return
            except Exception:  # noqa: BLE001
                continue

    def local_path(self, url: str) -> Path | None:
        """Return the local file for this source if it is downloaded, else kick off a
        background download and return None until it is ready."""
        key = source_key(url)
        cached = self._cached_file(key)
        if cached is not None:
            self._state[key] = {"status": "ready", "progress": 100.0}
            return cached
        with self._lock:
            th = self._threads.get(key)
            if th is None or not th.is_alive():
                self._state[key] = {"status": "downloading", "progress": 0.0}
                th = threading.Thread(target=self._do_download, args=(key, url),
                                      name=f"MediaDownload({key})", daemon=True)
                self._threads[key] = th
                th.start()
        return None

    def _do_download(self, key: str, url: str) -> Path | None:
        def progress(pct: float) -> None:
            st = self._state.get(key)
            if st is not None:
                st["progress"] = round(max(0.0, min(100.0, float(pct))), 1)
        try:
            with self._dl_sem:                       # one download at a time (default)
                path = self._download(url, self._dir, key, self._max_h, progress)
            self._state[key] = {"status": "ready", "progress": 100.0}
            log.info("media ready: %s -> %s", url, path)
            return path
        except Exception as exc:  # noqa: BLE001 - surfaced as 'failed' state, not a crash
            self._state[key] = {"status": "failed", "progress": 0.0,
                                "error": str(exc).splitlines()[0] if str(exc) else "error"}
            log.warning("media download failed for %s: %s", url, exc)
            return None

    def state(self, url: str) -> dict:
        """UI-facing state: {status: idle|downloading|ready|failed, progress: 0..100}."""
        key = source_key(url)
        if self._cached_file(key) is not None:
            return {"status": "ready", "progress": 100.0}
        return dict(self._state.get(key, {"status": "idle", "progress": 0.0}))


def _ytdlp_download(url: str, out_dir: Path, key: str, max_h: int,
                    progress_cb: Callable[[float], None]) -> Path:
    """Real downloader: yt-dlp, <= max_h, video-only mp4 preferred. Reuses ytstream's
    cookie / player-client fallbacks to clear YouTube's bot gate."""
    import yt_dlp

    from .ytstream import _ATTEMPTS, _COOKIE_BROWSERS, _cookie_file

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                progress_cb(100.0 * done / total)
        elif d.get("status") == "finished":
            progress_cb(100.0)

    base = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": _format_selector(max_h),
        "outtmpl": str(out_dir / f"{key}.%(ext)s"),
        "progress_hooks": [hook],
        "overwrites": True,
    }
    attempts: list[dict] = []
    cf = _cookie_file()
    if cf:
        attempts.append({"cookiefile": cf})
    attempts += list(_ATTEMPTS) + [{"cookiesfrombrowser": (b,)} for b in _COOKIE_BROWSERS]

    last_err: Exception | None = None
    for extra in attempts:
        try:
            with yt_dlp.YoutubeDL({**base, **extra}) as ydl:
                info = ydl.extract_info(url, download=True)
                path = Path(ydl.prepare_filename(info))
            if path.exists() and path.stat().st_size > 0:
                return path
            # some formats change the final ext; fall back to whatever landed on disk
            for p in sorted(out_dir.glob(f"{key}.*")):
                if p.suffix.lower() != ".part" and p.stat().st_size > 0:
                    return p
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise RuntimeError(f"yt-dlp failed: {last_err}")
