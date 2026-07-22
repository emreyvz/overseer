from forensic.query import ForensicQuery, parse_query


def test_color_and_garment_tr_en() -> None:
    q: ForensicQuery = parse_query("red jacket")
    assert q.colors == ["red"]
    assert q.clothing_types == ["jacket"]
    assert q.has_attribute_filters() and not q.has_event_filters()

    q2 = parse_query("red jacket")
    assert q2.colors == ["red"] and q2.clothing_types == ["jacket"]


def test_multiword_accessory_not_split() -> None:
    q = parse_query("blue backpack")
    assert q.colors == ["blue"]
    assert q.accessories == ["backpack"]
    assert q.unmatched == []


def test_event_type_terms() -> None:
    q = parse_query("vehicle")
    assert q.event_types == ["VEHICLE"]
    assert q.has_event_filters() and not q.has_attribute_filters()


def test_mixed_query() -> None:
    q = parse_query("red jacket vehicle")
    assert q.colors == ["red"]
    assert q.clothing_types == ["jacket"]
    assert q.event_types == ["VEHICLE"]


def test_height_build_and_backpack_en() -> None:
    q = parse_query("tall slim backpack")
    assert q.height_bands == ["tall"]
    assert q.builds == ["slim"]
    assert q.accessories == ["backpack"]


def test_deferred_and_unmatched() -> None:
    q = parse_query("running person zzz")
    assert q.event_types == ["PERSON"]
    assert "running" in q.deferred_terms
    assert "zzz" in q.unmatched


def test_empty() -> None:
    q = parse_query("   ")
    assert not q.has_attribute_filters() and not q.has_event_filters()
    assert q.unmatched == []


def test_dedup() -> None:
    q = parse_query("red red red")
    assert q.colors == ["red"]


def test_medium_height() -> None:
    q = parse_query("orta")
    assert q.height_bands == ["medium"]
    assert q.builds == []
    assert q.unmatched == []


def test_medium_height_qualified() -> None:
    q = parse_query("orta boy")
    assert q.height_bands == ["medium"]


def test_medium_build_qualified() -> None:
    q = parse_query("medium build")
    assert q.builds == ["medium"]
    assert q.height_bands == []


def test_medium_english() -> None:
    q = parse_query("medium")
    assert q.height_bands == ["medium"]
