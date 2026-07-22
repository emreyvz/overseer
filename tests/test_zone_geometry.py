from zones.model import ZONE_TYPES, ZoneView, point_in_polygon, segment_intersects, side_sign

_SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]


def test_point_in_polygon_inside_outside() -> None:
    assert point_in_polygon((5, 5), _SQUARE) is True
    assert point_in_polygon((15, 5), _SQUARE) is False
    assert point_in_polygon((-1, 5), _SQUARE) is False


def test_point_in_polygon_degenerate() -> None:
    assert point_in_polygon((5, 5), [(0, 0), (1, 1)]) is False  # <3 vertices


def test_segment_intersects() -> None:
    assert segment_intersects((0, 5), (10, 5), (5, 0), (5, 10)) is True   # cross
    assert segment_intersects((0, 0), (1, 1), (5, 5), (6, 6)) is False    # apart
    assert segment_intersects((0, 0), (10, 0), (0, 1), (10, 1)) is False  # parallel


def test_side_sign() -> None:
    # line a->b along +x; point above (larger y) vs below
    assert side_sign((0, 0), (10, 0), (5, 5)) == side_sign((0, 0), (10, 0), (1, 3))
    assert side_sign((0, 0), (10, 0), (5, 5)) != side_sign((0, 0), (10, 0), (5, -5))


def test_zoneview_and_types() -> None:
    assert "restricted" in ZONE_TYPES and "line" in ZONE_TYPES
    zv = ZoneView(1, "Door", "entrance", _SQUARE, 2, 5, 3)
    assert zv.occupancy == 2 and zv.entries == 5


def test_segment_intersects_order_independent() -> None:
    # a track point moving across a vertical trip-wire, touching it exactly
    p1, p2 = (5, 0), (5, 5)
    a, b = (0, 0), (10, 0)
    # touching an endpoint is NOT a proper crossing, and must be order-stable
    assert segment_intersects(p1, p2, a, b) == segment_intersects(p1, p2, b, a)
    # a clean crossing is detected regardless of line endpoint order
    q1, q2 = (5, -5), (5, 5)
    assert segment_intersects(q1, q2, a, b) is True
    assert segment_intersects(q1, q2, b, a) is True


def test_queue_type_and_allowed_direction() -> None:
    assert "queue" in ZONE_TYPES
    zv = ZoneView(1, "Door", "line", [(0, 0), (10, 0)], 0, 0, 0,
                  allowed_direction="a->b")
    assert zv.allowed_direction == "a->b"
    # default keeps existing 7-arg construction working
    zv2 = ZoneView(2, "Lobi", "lobby", [(0, 0), (1, 0), (1, 1)], 1, 2, 1)
    assert zv2.allowed_direction is None
