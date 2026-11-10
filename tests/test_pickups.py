import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from expstrip.vision.pickups import (PickupCounter, parse_pickup, pickup_rect, read_pickup_lines,
                                     region_changed)

FIX = Path(__file__).parent / "fixtures" / "pickups"


@pytest.mark.parametrize("text,val", [("You have gained mesos (+12)", 12), ("You have gained mesos (+1,203)", 1203),
                                      ("You have gained an item (Lemon)", None), ("You have gained mesos (+1", None),
                                      ("You received EXP (+66)", None), ("Tablecloth x1 earned. (Etc)", None)])
def test_parse_pickup(text, val):
    assert parse_pickup(text) == val


def test_counter_equal_amounts_counted_twice():
    c = PickupCounter()
    assert c.feed([(13, 100.0)]) == [13]
    assert c.feed([(13, 80.0), (13, 100.0)]) == [13]       # older line moved up, a new +13 below


def test_counter_burst_and_fade():
    c = PickupCounter()
    c.feed([(12, 60.0), (11, 80.0), (12, 100.0)])
    assert c.feed([(11, 40.0), (12, 60.0), (10, 80.0), (10, 100.0)]) == [10, 10]   # top faded, two new
    assert c.feed([(11, 40.0), (12, 60.0), (10, 80.0), (10, 100.0)]) == []          # unchanged frame


def test_counter_survives_a_frame_with_nothing_read():
    c = PickupCounter()
    assert c.feed([(44, 224.0)]) == [44]
    assert c.feed([]) == []                                  # e.g. a mob covered the line
    assert c.feed([(44, 198.0)]) == []                       # same line, moved up one slot


def test_counter_forgets_after_several_empty_frames():
    c = PickupCounter()
    c.feed([(44, 224.0)])
    for _ in range(PickupCounter.MAX_AGE + 1):
        c.feed([])
    assert c.feed([(44, 224.0)]) == [44]                     # the old line faded long ago


def test_counter_newest_line_missing_for_one_frame_is_not_recounted():
    c = PickupCounter()
    assert c.feed([(68, 172.0), (74, 224.0)]) == [68, 74]
    assert c.feed([(68, 172.0)]) == []                       # bottom line rejected/covered once
    assert c.feed([(68, 172.0), (74, 224.0)]) == []          # it is back: still the same line


def test_counter_lone_misread_does_not_recount_the_real_value():
    c = PickupCounter()
    assert c.feed([(74, 224.0)]) == [74]
    # A lone different value at the same y cannot be told from a new line that took the
    # slot the old one just left (meso_a frame 109: 79 then a real 74), so it counts once.
    assert c.feed([(14, 224.0)]) == [14]
    assert c.feed([(74, 224.0)]) == []    # read correctly again: matches the remembered 74


def test_counter_new_line_in_the_slot_an_old_one_left_is_counted():
    c = PickupCounter()
    assert c.feed([(79, 224.0)]) == [79]
    assert c.feed([(74, 224.0)]) == [74]  # 79 moved up unread; a new 74 at the bottom
    assert c.feed([(79, 171.0)]) == []    # the 79 again, two slots up: still the same line


def test_counter_prefers_the_shift_of_just_read_lines():
    c = PickupCounter()
    c.feed([(40, 172.0), (79, 198.0)])
    c.feed([(79, 198.0)])
    assert c.feed([(74, 224.0)]) == [74]
    # 79 (last read two frames ago) and 74 (last frame) both fit a shift; the 74's wins
    assert c.feed([(79, 145.0), (74, 198.0)]) == []


def test_counter_lines_after_old_ones_faded_are_new():
    c = PickupCounter()
    assert c.feed([(79, 198.0)]) == [79]
    assert c.feed([(74, 172.0)]) == [74]  # 79 faded out above; 74 arrived (pushed up by another line)


def test_counter_reset_forgets_lines():
    c = PickupCounter()
    c.feed([(13, 100.0)])
    c.reset()
    assert c.feed([(13, 100.0)]) == [13]


def test_pickup_rect_scales_from_bottom_right():
    r1 = pickup_rect(np.zeros((1440, 2560, 3), np.uint8), 1.0)
    r2 = pickup_rect(np.zeros((720, 1280, 3), np.uint8), 0.5)
    assert r1 is not None and r2 is not None
    assert r1.x + r1.w <= 2560 and r1.y + r1.h <= 1440
    assert abs((2560 - r1.x) / 2 - (1280 - r2.x)) <= 1 and abs((1440 - r1.y) / 2 - (720 - r2.y)) <= 1
    assert abs(r1.w / 2 - r2.w) <= 1 and abs(r1.h / 2 - r2.h) <= 1


def test_region_changed():
    a = np.zeros((200, 400, 3), np.uint8)
    b = a.copy()
    assert region_changed(None, a)
    assert not region_changed(a, b)
    cv2.putText(b, "You have gained mesos (+12)", (100, 150), cv2.FONT_HERSHEY_PLAIN, 1, (255, 255, 255), 1)
    assert region_changed(a, b)


def test_fixture_sequence_matches_truth():
    truth = json.loads((FIX / "truth.json").read_text())
    c = PickupCounter()
    prev = None
    got = {}
    for name in truth["frames"]:
        crop = cv2.imread(str(FIX / name))
        if region_changed(prev, crop):
            new = c.feed(read_pickup_lines(crop, 1.0))
            if new:
                got[name] = new
        prev = crop
    assert got == truth["new"]


def test_counter_generic_keys():
    from expstrip.vision.items import ItemLine as L
    k = ("i", L("Tablecloth", 1, "Etc"))
    c = PickupCounter()
    assert c.feed([(k, 100.0)]) == [k]
    assert c.feed([(k, 100.0)]) == []
    assert c.feed([(k, 73.5), (k, 100.0)]) == [k]


def test_pile_of_items_in_one_frame_each_counted():
    from expstrip.vision.items import ItemLine as L
    c = PickupCounter()
    m1, m2 = ("m", 12), ("m", 30)
    assert c.feed([(m1, 40.0), (m2, 66.5)]) == [m1, m2]
    a, b, d = (("i", L("Tablecloth", 1, "Etc")), ("i", L("Stirge Wing", 1, "Etc")),
               ("i", L("Tablecloth", 1, "Etc")))
    # the two meso lines scrolled up three lines; three item lines appear below them
    got = c.feed([(m1, 40.0 - 79.5), (m2, 66.5 - 79.5), (a, 40.0), (b, 66.5), (d, 93.0)])
    assert got == [a, b, d]
