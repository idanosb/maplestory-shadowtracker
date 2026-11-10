from expstrip.ui.format import format_duration, format_k


def test_format_k():
    assert format_k(950) == "950"
    assert format_k(48600) == "48.6K"
    assert format_k(129300) == "129K"
    assert format_k(1234567) == "1.23M"
    assert format_k(4200, signed=True) == "+4.2K"
    assert format_k(-1100, signed=True) == "-1.1K"
    assert format_k(0, signed=True) == "+0"
    assert format_k(None) == "—"


def test_format_duration():
    assert format_duration(None) == "—"
    assert format_duration(42 * 60) == "42m"
    assert format_duration(15660) == "4h 21m"
    assert format_duration(30) == "<1m"
    assert format_duration(200 * 3600) == "99h+"
