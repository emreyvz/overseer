from camera.mjpeg_parser import MJPEGParser

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def fake_jpeg(payload: bytes = b"A" * 100) -> bytes:
    return SOI + payload + EOI


def multipart_wrap(jpeg: bytes) -> bytes:
    return (b"--myboundary\r\nContent-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")


def test_single_frame_in_one_chunk() -> None:
    p = MJPEGParser()
    frames = p.feed(multipart_wrap(fake_jpeg()))
    assert len(frames) == 1
    assert frames[0].startswith(SOI) and frames[0].endswith(EOI)


def test_frame_split_across_chunks() -> None:
    p = MJPEGParser()
    data = multipart_wrap(fake_jpeg())
    mid = len(data) // 2
    assert p.feed(data[:mid]) == []
    frames = p.feed(data[mid:])
    assert len(frames) == 1


def test_multiple_frames_in_one_chunk() -> None:
    p = MJPEGParser()
    data = multipart_wrap(fake_jpeg(b"1" * 50)) + multipart_wrap(fake_jpeg(b"2" * 60))
    frames = p.feed(data)
    assert len(frames) == 2
    assert frames[0] != frames[1]


def test_garbage_between_frames_ignored() -> None:
    p = MJPEGParser()
    data = b"\x00garbage\x00" + fake_jpeg() + b"noise" + fake_jpeg()
    frames = p.feed(data)
    assert len(frames) == 2


def test_orphan_eoi_counted_corrupt() -> None:
    p = MJPEGParser()
    frames = p.feed(b"junk" + EOI + fake_jpeg())
    assert len(frames) == 1
    assert p.corrupt_count == 1


def test_buffer_overflow_resets() -> None:
    p = MJPEGParser(max_buffer=1000)
    p.feed(SOI + b"X" * 2000)  # EOI never arrives
    assert len(p._buffer) == 0  # buffer was reset
    frames = p.feed(fake_jpeg())
    assert len(frames) == 1  # parser continues to work
