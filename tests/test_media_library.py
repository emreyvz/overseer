import time
from pathlib import Path

from server.media import MediaLibrary, source_key, youtube_id


def test_source_key_from_youtube_forms() -> None:
    assert source_key("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert source_key("https://youtu.be/dQw4w9WgXcQ?t=5") == "dQw4w9WgXcQ"
    assert source_key("https://youtube.com/live/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    # non-youtube -> stable hash key, same every time
    k = source_key("http://cam.local/stream.mjpg")
    assert k.startswith("u") and source_key("http://cam.local/stream.mjpg") == k


def _fake_downloader(calls):
    def dl(url, out_dir, key, max_h, progress_cb):
        calls.append(key)
        progress_cb(0.0)
        progress_cb(100.0)
        p = Path(out_dir) / f"{key}.mp4"
        p.write_bytes(b"FAKEMP4")
        return p
    return dl


def test_download_then_ready(tmp_path: Path) -> None:
    calls: list[str] = []
    lib = MediaLibrary(tmp_path, downloader=_fake_downloader(calls))
    url = "https://youtu.be/dQw4w9WgXcQ"
    assert lib.local_path(url) is None            # kicks off background download
    lib._threads[source_key(url)].join(timeout=3)
    path = lib.local_path(url)
    assert path is not None and path.exists()
    assert lib.state(url)["status"] == "ready"
    assert calls == ["dQw4w9WgXcQ"]


def test_cache_hit_does_not_redownload(tmp_path: Path) -> None:
    calls: list[str] = []
    lib = MediaLibrary(tmp_path, downloader=_fake_downloader(calls))
    key = source_key("https://youtu.be/dQw4w9WgXcQ")
    (tmp_path / f"{key}.mp4").write_bytes(b"ALREADY")   # pretend it's already downloaded
    path = lib.local_path("https://youtu.be/dQw4w9WgXcQ")
    assert path is not None
    assert calls == []                            # never called the downloader
    assert lib.state("https://youtu.be/dQw4w9WgXcQ")["status"] == "ready"


def test_failed_download_sets_state(tmp_path: Path) -> None:
    def boom(url, out_dir, key, max_h, progress_cb):
        raise RuntimeError("bot gate")
    lib = MediaLibrary(tmp_path, downloader=boom)
    url = "https://youtu.be/dQw4w9WgXcQ"
    lib._do_download(source_key(url), url)        # run synchronously
    st = lib.state(url)
    assert st["status"] == "failed"
    assert lib.local_path(url) is None


def test_youtube_id() -> None:
    assert youtube_id("https://youtu.be/dQw4w9WgXcQ?t=5") == "dQw4w9WgXcQ"
    assert youtube_id("http://cam.local/stream") is None


def test_cached_path_does_not_trigger_download(tmp_path: Path) -> None:
    calls: list[str] = []
    lib = MediaLibrary(tmp_path, downloader=_fake_downloader(calls))
    assert lib.cached_path("https://youtu.be/dQw4w9WgXcQ") is None
    assert calls == []                            # merely checking must NOT start a download


def test_cached_path_returns_downloaded(tmp_path: Path) -> None:
    lib = MediaLibrary(tmp_path)
    key = source_key("https://youtu.be/dQw4w9WgXcQ")
    (tmp_path / f"{key}.mp4").write_bytes(b"VIDEO")
    assert lib.cached_path("https://youtu.be/dQw4w9WgXcQ") == tmp_path / f"{key}.mp4"


def test_cached_file_ignores_poster_and_part(tmp_path: Path) -> None:
    lib = MediaLibrary(tmp_path)
    key = source_key("https://youtu.be/dQw4w9WgXcQ")
    (tmp_path / f"{key}.poster.jpg").write_bytes(b"\xff\xd8IMG")
    (tmp_path / f"{key}.mp4.part").write_bytes(b"PARTIAL")
    assert lib.cached_path("https://youtu.be/dQw4w9WgXcQ") is None   # neither is a video
    (tmp_path / f"{key}.mp4").write_bytes(b"VIDEO")
    assert lib.cached_path("https://youtu.be/dQw4w9WgXcQ") is not None


def test_poster_returns_cached_bytes(tmp_path: Path) -> None:
    lib = MediaLibrary(tmp_path)
    vid = "dQw4w9WgXcQ"
    (tmp_path / f"{vid}.poster.jpg").write_bytes(b"\xff\xd8POSTER")
    assert lib.poster(f"https://youtu.be/{vid}") == b"\xff\xd8POSTER"
    assert lib.poster("http://cam.local/stream") is None    # non-youtube -> no poster


def test_downloads_are_serialized(tmp_path: Path) -> None:
    import threading
    counts = {"now": 0, "max": 0}
    clock = threading.Lock()
    gate = threading.Event()

    def slow(url, out_dir, key, max_h, cb):
        with clock:
            counts["now"] += 1
            counts["max"] = max(counts["max"], counts["now"])
        gate.wait(1.5)                    # hold so a second download would overlap if allowed
        with clock:
            counts["now"] -= 1
        p = Path(out_dir) / f"{key}.mp4"
        p.write_bytes(b"X")
        return p

    lib = MediaLibrary(tmp_path, downloader=slow, max_concurrent=1)
    lib.local_path("https://youtu.be/aaaaaaaaaaa")
    lib.local_path("https://youtu.be/bbbbbbbbbbb")
    time.sleep(0.25)                      # both threads live; the semaphore lets only one in
    gate.set()
    for k in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        th = lib._threads.get(k)
        if th:
            th.join(timeout=3)
    assert counts["max"] == 1             # never two downloads at once


def test_dedup_one_download(tmp_path: Path) -> None:
    import threading
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow(url, out_dir, key, max_h, progress_cb):
        calls.append(key)
        started.set()
        release.wait(2.0)
        p = Path(out_dir) / f"{key}.mp4"
        p.write_bytes(b"X")
        return p

    lib = MediaLibrary(tmp_path, downloader=slow)
    url = "https://youtu.be/dQw4w9WgXcQ"
    lib.local_path(url)
    started.wait(2.0)
    lib.local_path(url)                           # second call while first is in-flight
    release.set()
    lib._threads[source_key(url)].join(timeout=3)
    assert calls == ["dQw4w9WgXcQ"]               # only one download ran
