import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from expstrip.vision.items import ItemLine, match_name, read_item_lines
from expstrip.vision.pickups import PickupCounter

FIX = Path(__file__).parent / "fixtures" / "items"
TRUTH = json.loads((FIX / "truth.json").read_text())
NAMES = TRUTH["names"]


def _read(name: str, names=NAMES) -> list[tuple[str, int, str]]:
    crop = cv2.imread(str(FIX / name))
    assert crop is not None, name
    return [(k.name, k.qty, k.tab) for k, _ in read_item_lines(crop, 1.0, names)]


@pytest.mark.parametrize("frame", sorted(TRUTH["frames"]))
def test_lines_match_truth(frame):
    assert _read(frame) == [tuple(t) for t in TRUTH["frames"][frame]]


def test_lines_are_top_to_bottom_with_centre_y():
    lines = read_item_lines(cv2.imread(str(FIX / "loot_a_0060.png")), 1.0, NAMES)
    ys = [y for _, y in lines]
    assert ys == sorted(ys) and all(abs((b - a) - 26.5) < 2 for a, b in zip(ys, ys[1:]))


def test_party_exp_lines_are_not_items():
    assert _read("loot_a_0035.png") == []      # only "You received EXP" / "Bonus EXP for PARTY"


def test_meso_line_is_not_an_item():
    assert _read("loot_a_0037.png") == []      # "You have gained mesos (+45)" and EXP lines
    assert _read("loot_a_0043.png") == [("Stirge Wing", 1, "Etc")]   # the meso line between is left out


def test_unknown_name_ignored():
    assert _read("loot_a_0060.png", ["Tablecloth"]) == []
    assert _read("loot_a_0197.png", ["Tablecloth"]) == [("Tablecloth", 1, "Etc"), ("Tablecloth", 1, "Etc")]


def test_use_tab_line():
    # Blue Potion is not in the fixture price list; with it, its "(Use)" line is read.
    assert _read("loot_a_0043.png", ["Blue Potion", "Stirge Wing"]) == [("Blue Potion", 1, "Use"), ("Stirge Wing", 1, "Etc")]


def test_similar_name_is_not_taken_for_another():
    # "Orange Potion" ends like "Blue Potion"; it has its own template, so it isn't read as Blue.
    assert _read("loot_a_0146.png", ["Blue Potion", "Tablecloth"]) == [("Tablecloth", 1, "Etc")]


def test_cut_name_line_resolves_by_its_visible_start():
    assert ("Animal Fur Bunny Ears", 1, "Etc") in _read("loot_a_0049.png", ["Animal Fur Bunny Ears", "Tablecloth"])


def test_match_name():
    n = ["Tablecloth", "Stirge Wing"]
    assert match_name("Tablecloth", n) == "Tablecloth"
    assert match_name("Tablec...", n) == "Tablecloth"     # >= 6 letters, unique
    assert match_name("Tab...", n) is None                # < 6 letters
    assert match_name("Stirge...", ["Stirge Wing", "Stirge Fang"]) is None   # not unique
    assert match_name("Iron Ore", n) is None
    assert match_name("Animal Fur Bun...", ["Animal Fur Bunny Ears"]) == "Animal Fur Bunny Ears"


def test_blank_crop():
    assert read_item_lines(np.zeros((242, 422, 3), np.uint8), 1.0, NAMES) == []
    assert read_item_lines(np.zeros((0, 0, 3), np.uint8), 1.0, NAMES) == []


def test_scaled_crop_reads_the_same():
    crop = cv2.imread(str(FIX / "loot_a_0060.png"))
    half = cv2.resize(crop, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)
    assert [(k.name, k.qty) for k, _ in read_item_lines(half, 0.75, NAMES)] == [("Stirge Wing", 1)] * 3


def test_with_level_adds_brightness():
    lines = read_item_lines(cv2.imread(str(FIX / "loot_a_0060.png")), 1.0, NAMES, with_level=True)
    assert len(lines) == 3 and all(len(t) == 3 and 0 < t[2] <= 255 for t in lines)
    assert lines[0][2] < lines[2][2]           # the older line (top) has faded more


# ---- PickupCounter with item keys and levels ---------------------------------------------------
TC = ItemLine("Tablecloth", 1, "Etc")
SW = ItemLine("Stirge Wing", 1, "Etc")


def test_counter_item_keys_equal_lines_counted_twice():
    c = PickupCounter()
    assert c.feed([(TC, 225.0)]) == [TC]
    assert c.feed([(TC, 198.0), (TC, 225.0)]) == [TC]   # older one moved up, a second one below
    assert c.feed([(TC, 172.0), (TC, 198.0), (SW, 225.0)]) == [SW]   # both moved up, a Stirge Wing


def test_counter_brighter_line_in_a_remembered_slot_is_new():
    # loot_a 170-172: a Tablecloth at y 172, a frame with nothing read, then a brighter
    # Tablecloth at the same y: the first one scrolled up out of view meanwhile.
    c = PickupCounter()
    assert c.feed([(TC, 172.0, 106.0)]) == [TC]
    assert c.feed([]) == []
    assert c.feed([(TC, 172.0, 126.0)]) == [TC]


def test_counter_fading_line_is_not_new():
    c = PickupCounter()
    assert c.feed([(TC, 198.0, 195.0)]) == [TC]
    assert c.feed([(TC, 198.0, 160.0)]) == []
    assert c.feed([(TC, 172.0, 123.0)]) == []


def test_counter_covered_read_keeps_the_level_bound():
    # A mob over the line lowers one read; the uncovered read after it is still the same line.
    c = PickupCounter()
    assert c.feed([(TC, 146.0, 180.0)]) == [TC]
    assert c.feed([(TC, 146.0, 40.0)]) == []
    assert c.feed([(TC, 146.0, 150.0)]) == []


def test_counter_mixes_keys_with_and_without_level():
    c = PickupCounter()
    assert c.feed([(TC, 172.0, 150.0), (40, 198.0)]) == [TC, 40]
    assert c.feed([(TC, 146.0, 110.0), (40, 172.0), (40, 198.0)]) == [40]


def test_curse_eye_tail_learned_from_tail_a():
    # recordings/tail_a, cropped like the loot_a fixtures. 0070: Curse Eye Tail above an
    # Iron Ore line (not priced); 0081: a Blue Potion (Use) line above a faded Curse Eye Tail.
    names = NAMES + ["Curse Eye Tail"]
    assert _read("tail_a_0070.png", names) == [("Curse Eye Tail", 1, "Etc")]
    assert _read("tail_a_0081.png", names) == [("Curse Eye Tail", 1, "Etc")]
