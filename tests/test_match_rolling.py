import numpy as np

from match.rolling import RollingFrameStore


def _f(v=1):
    return np.full((4, 4, 3), v, dtype=np.uint8)


def test_window_respects_time() -> None:
    store = RollingFrameStore(window_seconds=3.0, max_frames=100)
    store.add(1, _f(1), now=0.0)
    store.add(1, _f(2), now=2.0)
    store.add(1, _f(3), now=10.0)
    # at now=10.5 only frames within 3s (the now=10.0 one) qualify
    win = store.window(1, now=10.5)
    assert len(win) == 1
    assert win[0][0, 0, 0] == 3


def test_max_frames_cap() -> None:
    store = RollingFrameStore(window_seconds=1e9, max_frames=5)
    for i in range(10):
        store.add(1, _f(i), now=float(i))
    win = store.window(1, now=100.0)
    assert len(win) == 5           # oldest dropped by the ring buffer


def test_sources_and_clear() -> None:
    store = RollingFrameStore()
    store.add(1, _f(), now=0.0)
    store.add(2, _f(), now=0.0)
    assert set(store.sources()) == {1, 2}
    store.clear(1)
    assert set(store.sources()) == {2}
    store.clear()
    assert store.sources() == []


def test_empty_frame_ignored() -> None:
    store = RollingFrameStore()
    store.add(1, np.empty((0, 0, 3), dtype=np.uint8), now=0.0)
    assert store.window(1, now=0.0) == []


def test_unknown_source_empty() -> None:
    store = RollingFrameStore()
    assert store.window(99, now=0.0) == []
