from expstrip.reading import (MapName, Reading, ReadingValidator,
                              normalize_map_name, parse_exp_text)


def test_parse_exp_text():
    assert parse_exp_text("114,698 [26.78%]") == (114698, 26.78)
    assert parse_exp_text(" 2,612,779 [91.96%] ") == (2612779, 91.96)
    assert parse_exp_text("0 [0.00%]") == (0, 0.0)
    assert parse_exp_text("114,69 [26.78%]") is None   # bad grouping
    assert parse_exp_text("114698 26.78") is None


def test_normalize_map_name():
    assert normalize_map_name(["Victoria Road : Kerning City Pharmacy"]) == MapName("Victoria Road", "Kerning City Pharmacy")
    assert normalize_map_name(["Victoria Road", "Kerning City"]) == MapName("Victoria Road", "Kerning City")
    assert normalize_map_name(["  Ant  Tunnel I "]) == MapName("", "Ant Tunnel I")
    assert normalize_map_name([]) is None


def test_map_full():
    assert MapName("A", "B").full == "A : B"
    assert MapName("", "B").full == "B"


def test_validator_needs_two_agreeing_frames():
    v = ReadingValidator(); m = MapName("Victoria Road", "Kerning City")
    assert v.feed(44, (114698, 26.78), m) is None
    assert v.feed(44, (114698, 26.78), m) == Reading(44, 114698, 26.78, m)


def test_validator_ignores_single_outlier_frame():
    v = ReadingValidator(); m = MapName("", "A")
    for _ in range(2): v.feed(44, (114698, 26.78), m)
    assert v.feed(44, (914698, 26.78), m) is None
    # a new value whose EXP and pct agree, and that went up, is accepted at once
    assert v.feed(44, (114900, 26.83), m).exp == 114900


def test_validator_map_flicker_does_not_change_map():
    v = ReadingValidator(); a = MapName("", "Ant Tunnel I"); b = MapName("", "Ant Tunnel l")
    for _ in range(2): v.feed(44, (100, 0.02), a)
    assert v.feed(44, (100, 0.02), b).map == a
    assert v.feed(44, (100, 0.02), a).map == a


def test_validator_none_map_keeps_previous():
    v = ReadingValidator(); a = MapName("", "A")
    for _ in range(2): v.feed(44, (100, 0.02), a)
    assert v.feed(44, (100, 0.02), None).map == a


def test_validator_no_lockup_after_exp_jump():
    v = ReadingValidator(); m = MapName("", "A")
    for _ in range(2): v.feed(44, (6426, 1.50), m)
    assert v.requirement(44) == 428400
    # point estimate predicts 30.00 (off by 0.10pp); rounding bounds allow it
    assert v.feed(44, (128520, 30.10), m).exp == 128520


def test_validator_escape_hatch_relearns():
    v = ReadingValidator(); m = MapName("", "A")
    for _ in range(2): v.feed(44, (100000, 25.00), m)
    assert v.requirement(44) == 400000
    bad = (200000, 25.00)
    assert v.feed(44, bad, m) is None
    assert v.feed(44, bad, m) is None
    r = v.feed(44, bad, m)
    assert r is not None and r.exp == 200000
    assert v.requirement(44) == 800000


def test_requirement_not_learned_below_1pct():
    v = ReadingValidator(); m = MapName("", "A")
    for _ in range(2): v.feed(44, (100, 0.02), m)
    assert v.requirement(44) is None


def test_new_level_accepted_without_requirement():
    v = ReadingValidator(); m = MapName("", "A")
    for _ in range(2): v.feed(44, (100000, 25.00), m)
    assert v.feed(45, (10, 0.00), m) is None
    assert v.feed(45, (10, 0.00), m) == Reading(45, 10, 0.0, m)


def test_validator_accepts_exp_that_changes_every_frame():
    """Regression (live, fast mob grinding): EXP rose almost every second, so two identical
    frames in a row were rare and most of the time went untracked."""
    v = ReadingValidator(); m = MapName("Hidden Street", "Monkey Swamp I")
    for _ in range(2): v.feed(44, (295193, 68.93), m)
    seq = [(295219, 68.93), (295232, 68.93), (295291, 68.95), (295337, 68.96), (295409, 68.98)]
    assert [v.feed(44, ep, m).exp for ep in seq] == [e for e, _ in seq]


def test_validator_single_frame_drop_still_needs_confirmation():
    """A lower EXP (a death, or a misread) is not trusted from one frame."""
    v = ReadingValidator(); m = MapName("", "A")
    for _ in range(2): v.feed(44, (295409, 68.98), m)
    assert v.feed(44, (295400, 68.98), m) is None
    assert v.feed(44, (295400, 68.98), m).exp == 295400


def test_validator_fast_path_needs_learned_requirement():
    v = ReadingValidator(); m = MapName("", "A")
    for _ in range(2): v.feed(44, (100, 0.02), m)   # below 1%: nothing learned
    assert v.feed(44, (150, 0.03), m) is None
