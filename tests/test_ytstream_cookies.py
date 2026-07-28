from server.ytstream import _valid_cookie_file


def test_empty_file_is_invalid(tmp_path) -> None:
    p = tmp_path / "c.txt"
    p.write_text("", encoding="utf-8")
    assert _valid_cookie_file(str(p)) is False        # the 0-byte placeholder footgun


def test_blank_and_comment_only_is_invalid(tmp_path) -> None:
    p = tmp_path / "c.txt"
    p.write_text("\n   \n# just a note\n", encoding="utf-8")
    assert _valid_cookie_file(str(p)) is False


def test_netscape_header_is_valid(tmp_path) -> None:
    p = tmp_path / "c.txt"
    p.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert _valid_cookie_file(str(p)) is True


def test_tab_delimited_row_is_valid(tmp_path) -> None:
    p = tmp_path / "c.txt"
    p.write_text(".youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tval\n", encoding="utf-8")
    assert _valid_cookie_file(str(p)) is True


def test_missing_file_is_invalid(tmp_path) -> None:
    assert _valid_cookie_file(str(tmp_path / "nope.txt")) is False
