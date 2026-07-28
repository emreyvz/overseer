"""YouTube live-stream source support.

Resolves a YouTube (or other yt-dlp-supported) watch/live URL to a direct media
URL that OpenCV's FFMPEG backend can open (HLS m3u8 preferred). Live URLs are
signed and expire, so we resolve fresh on connect and cache briefly by URL."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger("overseer.yt")

# A cookies.txt exported from a browser signed into YouTube is the reliable way to
# clear the "confirm you're not a bot" gate. Drop it at one of these paths (or set
# OVERSEER_YT_COOKIES) and it's used first. No password ever leaves the machine.
_COOKIE_CANDIDATES = [
    os.environ.get("OVERSEER_YT_COOKIES", ""),
    str(Path(__file__).resolve().parent.parent / "config" / "youtube_cookies.txt"),
    str(Path(__file__).resolve().parent.parent / "youtube_cookies.txt"),
]


def _valid_cookie_file(p: str) -> bool:
    """A usable Netscape cookies.txt: exists, non-empty, and actually looks like one. An
    EMPTY placeholder file (a very common footgun — the app ships one, or the operator
    `touch`es it) makes yt-dlp abort every request with 'does not look like a Netscape
    format cookies file', so we must skip it and fall through to the browser-cookie and
    no-cookie attempts instead."""
    try:
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            return False
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                if s.startswith("# Netscape") or s.startswith("# HTTP Cookie"):
                    return True          # the canonical header
                if not s.startswith("#") and "\t" in line:
                    return True          # a real tab-delimited cookie row
        return False                     # only blanks/comments -> not a real cookie file
    except Exception:  # noqa: BLE001
        return False


def _cookie_file() -> str | None:
    for p in _COOKIE_CANDIDATES:
        if p and _valid_cookie_file(p):
            return p
    # Be forgiving about the exact name (e.g. Windows "youtube_cookies.txt.txt").
    root = Path(__file__).resolve().parent.parent
    for base in (root / "config", root):
        try:
            hits = sorted(str(h) for h in base.glob("*cookies*.txt*") if _valid_cookie_file(str(h)))
            if hits:
                return hits[0]
        except Exception:  # noqa: BLE001
            pass
    return None

# Resolved HLS URLs are signed but valid for hours — cache them so re-entering a
# YouTube camera is instant instead of paying the yt-dlp resolve cost every time.
_RESOLVE_CACHE: dict[str, tuple[str, float]] = {}
_RESOLVE_TTL = 2400.0  # 40 minutes

# Hosts we route through yt-dlp instead of opening directly.
_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")


def is_stream_url(url: str) -> bool:
    """True for URLs that must be resolved via yt-dlp (YouTube & friends)."""
    u = url.lower()
    return any(h in u for h in _YT_HOSTS)


_TARGET_H = 720  # cap resolution — 1080p+ decodes too slowly and stutters/freezes


def _pick(info: dict) -> str | None:
    fmts = info.get("formats") or []
    usable = [f for f in fmts if f.get("url") and (
        "m3u8" in (f.get("protocol") or "") or (f.get("protocol") or "").startswith("http"))]
    if not usable:
        return info.get("manifest_url") or info.get("url")

    # Prefer an HLS muxed rendition at ~720p: enough detail but light enough to
    # stay real-time. Ranked: HLS, muxed, resolution ≤ target (higher within),
    # over-target penalised, then bitrate.
    def score(f: dict) -> tuple:
        proto = f.get("protocol") or ""
        is_hls = 1 if "m3u8" in proto else 0
        muxed = 1 if (f.get("acodec") not in (None, "none") and f.get("vcodec") not in (None, "none")) else 0
        h = f.get("height") or 0
        under = 1 if 0 < h <= _TARGET_H else 0
        key_h = h if under else -h  # under-target: bigger is better; over-target: penalise
        return (is_hls, muxed, under, key_h, f.get("tbr") or 0)
    return max(usable, key=score)["url"]


# Extraction attempts, tried in order until one beats YouTube's "confirm you're not
# a bot" gate. First plain, then alternate player clients, then the user's browser
# cookies (the reliable bypass — works when a browser here is logged into YouTube).
_ATTEMPTS: list[dict] = [
    {},
    {"extractor_args": {"youtube": {"player_client": ["tv", "mweb", "web_safari", "android"]}}},
]
_COOKIE_BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "chromium")


def resolve_stream(url: str) -> str | None:
    """Return a direct media URL for a YouTube/yt-dlp source, or None on failure.
    Cached by URL so re-entering the same live feed doesn't re-run yt-dlp."""
    now = time.time()
    hit = _RESOLVE_CACHE.get(url)
    if hit is not None and hit[1] > now:
        return hit[0]
    try:
        import yt_dlp
    except Exception as exc:  # noqa: BLE001
        log.warning("yt-dlp unavailable: %s", exc)
        return None

    base = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    attempts: list[dict] = []
    cf = _cookie_file()
    if cf:  # an operator-provided cookies.txt is the most reliable — try it first
        attempts.append({"cookiefile": cf})
    attempts += list(_ATTEMPTS) + [{"cookiesfrombrowser": (b,)} for b in _COOKIE_BROWSERS]
    last_err: str = "unknown"
    for extra in attempts:
        opts = {**base, **extra}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc).splitlines()[0]
            continue
        direct = _pick(info)
        if direct:
            _RESOLVE_CACHE[url] = (direct, now + _RESOLVE_TTL)
            log.info("resolved YouTube source %s (%s)", url, "cookies" if "cookiesfrombrowser" in extra else "default")
            return direct
    log.warning("YouTube resolve failed for %s: %s", url, last_err)
    return None
