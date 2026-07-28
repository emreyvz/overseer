from ai.tiling import iou, select_supplements, tiles


def test_tiles_whole_frame_when_n1() -> None:
    assert tiles(100, 200, 1) == [(0, 0, 200, 100)]
    assert tiles(100, 200, 0) == [(0, 0, 200, 100)]


def test_tiles_grid_count_and_overlap() -> None:
    ts = tiles(100, 100, 2, overlap=0.2)
    assert len(ts) == 4                       # 2x2
    # tiles overlap: the first cell extends past the 50 midline by the overlap margin
    assert ts[0][2] > 50 and ts[0][3] > 50
    # every tile stays inside the frame
    for (x0, y0, x1, y1) in ts:
        assert 0 <= x0 < x1 <= 100 and 0 <= y0 < y1 <= 100


def test_iou() -> None:
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert abs(iou((0, 0, 10, 10), (5, 0, 15, 10)) - (50 / 150)) < 1e-9


def test_select_supplements_adds_only_new() -> None:
    existing = [(0, 0, 20, 20)]               # already tracked on the full frame
    cands = [
        ((0, 0, 20, 20), 0.9),                # duplicate of existing -> drop
        ((100, 100, 120, 120), 0.8),          # a genuinely new small object -> keep
        ((102, 102, 122, 122), 0.7),          # overlaps the new one (another tile) -> drop
        ((200, 200, 220, 220), 0.6),          # another new object -> keep
    ]
    keep = select_supplements(existing, cands, iou_thresh=0.45)
    assert set(keep) == {1, 3}


def test_select_supplements_empty() -> None:
    assert select_supplements([(0, 0, 10, 10)], []) == []
    # nothing existing -> all non-overlapping candidates kept
    keep = select_supplements([], [((0, 0, 10, 10), 0.5), ((50, 50, 60, 60), 0.4)])
    assert set(keep) == {0, 1}
