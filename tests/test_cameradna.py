from server.cameradna import CameraProfiles


def _feed(cp, sid, n, *, brightness, motion, fps, dets):
    for _ in range(n):
        cp.observe_frame(sid, brightness=brightness, motion=motion, fps=fps, dets=dets)


def test_pedestrian_vs_vehicle_dna() -> None:
    cp = CameraProfiles()
    _feed(cp, 1, 60, brightness=140, motion=4, fps=15, dets=[("person", 0.9), ("person", 0.85)])
    p = cp.profile(1, "Lobby")
    assert "pedestrian heavy" in p["dna"] and "vehicle heavy" not in p["dna"]
    cp2 = CameraProfiles()
    _feed(cp2, 2, 60, brightness=140, motion=4, fps=15, dets=[("vehicle", 0.9), ("vehicle", 0.8)])
    assert "vehicle heavy" in cp2.profile(2, "Road")["dna"]


def test_night_and_reputation() -> None:
    cp = CameraProfiles()
    _feed(cp, 1, 60, brightness=20, motion=3, fps=4, dets=[("person", 0.5)])
    for _ in range(5):
        cp.note_reconnect(1)
    p = cp.profile(1, "Alley")
    assert "night dominant" in p["dna"] and "low light" in p["dna"]
    assert 0.0 <= p["reputation"] < 0.6      # poor light, low fps, flaky -> low reputation
    cp2 = CameraProfiles()
    _feed(cp2, 2, 60, brightness=128, motion=4, fps=20, dets=[("person", 0.95)])
    assert cp2.profile(2, "Good")["reputation"] > p["reputation"]


def test_all_and_reset() -> None:
    cp = CameraProfiles()
    _feed(cp, 1, 5, brightness=120, motion=3, fps=12, dets=[("person", 0.8)])
    _feed(cp, 2, 5, brightness=120, motion=3, fps=12, dets=[("vehicle", 0.8)])
    rows = cp.all({1: "A", 2: "B"})
    assert {r["name"] for r in rows} == {"A", "B"}
    assert cp.profile(999)["frames"] == 0    # unknown camera -> empty profile
    cp.reset()
    assert cp.all() == []
