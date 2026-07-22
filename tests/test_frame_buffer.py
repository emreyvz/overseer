import numpy as np

from camera.frame_buffer import Frame, FrameBuffer


def make_frame(seq: int) -> Frame:
    return Frame(image=np.zeros((4, 4, 3), dtype=np.uint8), timestamp=float(seq), seq=seq)


def test_put_get_fifo() -> None:
    buf = FrameBuffer(maxsize=3)
    for i in range(3):
        assert buf.put(make_frame(i)) is True
    assert buf.get().seq == 0
    assert buf.get().seq == 1
    assert buf.qsize() == 1


def test_backpressure_drops_oldest() -> None:
    buf = FrameBuffer(maxsize=2)
    buf.put(make_frame(0))
    buf.put(make_frame(1))
    assert buf.put(make_frame(2)) is False  # oldest frame 0 dropped
    assert buf.dropped == 1
    assert buf.get().seq == 1
    assert buf.get().seq == 2


def test_get_timeout_returns_none() -> None:
    buf = FrameBuffer(maxsize=2)
    assert buf.get(timeout=0.05) is None


def test_clear() -> None:
    buf = FrameBuffer(maxsize=3)
    buf.put(make_frame(0))
    buf.clear()
    assert buf.qsize() == 0


def test_concurrent_producer_consumer() -> None:
    import threading

    buf = FrameBuffer(maxsize=5)
    consumed: list[int] = []
    stop = threading.Event()

    def consumer() -> None:
        while not stop.is_set() or buf.qsize() > 0:
            f = buf.get(timeout=0.05)
            if f is not None:
                consumed.append(f.seq)

    t = threading.Thread(target=consumer)
    t.start()
    for i in range(200):
        buf.put(make_frame(i))
    stop.set()
    t.join()
    # Dropped + consumed = produced; consumption should be ordered
    assert consumed == sorted(consumed)
    assert len(consumed) + buf.dropped == 200
