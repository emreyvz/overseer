from match.anpr.normalize import normalize_plate, plate_similarity, plates_match


def test_normalize_strips_and_uppercases() -> None:
    assert normalize_plate(" 34-abc 123 ") == "34ABC123"
    assert normalize_plate("b 1234 xyz") == "B1234XYZ"


def test_normalize_empty() -> None:
    assert normalize_plate("") == ""
    assert normalize_plate("!!!") == ""


def test_confusable_folding_optional() -> None:
    assert normalize_plate("O0I1", fold_confusable=False) == "O0I1"
    assert normalize_plate("O0I1", fold_confusable=True) == "0011"


def test_similarity_identical_and_disjoint() -> None:
    assert plate_similarity("34ABC123", "34 ABC 123") == 1.0
    assert plate_similarity("", "") == 1.0
    assert plate_similarity("ABC", "") == 0.0
    assert plate_similarity("AAAA", "BBBB") == 0.0


def test_similarity_one_char_off() -> None:
    # 8 chars, 1 edit -> 1 - 1/8 = 0.875
    assert abs(plate_similarity("34ABC123", "34ABC124") - 0.875) < 1e-9


def test_plates_match_threshold() -> None:
    assert plates_match("34ABC123", "34ABC124", threshold=0.85) is True
    assert plates_match("34ABC123", "99XYZ999", threshold=0.85) is False


def test_plates_match_with_confusable() -> None:
    # O vs 0 read error only matches when folding is enabled
    assert plates_match("34ABO123", "34AB0123", threshold=0.99) is False
    assert plates_match("34ABO123", "34AB0123", threshold=0.99,
                        fold_confusable=True) is True
