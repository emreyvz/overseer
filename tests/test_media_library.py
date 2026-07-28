from pathlib import Path

from server.media import MediaLibrary, source_key


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
