import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from expstrip.vision.quickslots import CountConfirmer, read_potion_counts

FIX = Path(__file__).parent / "fixtures"
TRUTH = json.loads((FIX / "meso_truth.json").read_text(encoding="utf-8"))
MESO_FIXTURES = sorted(TRUTH)


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(FIX / f"{name}.png"))
    assert img is not None, name
    return img


@pytest.mark.parametrize("name", MESO_FIXTURES)
def test_counts_match_truth(name):
    assert read_potion_counts(load(name), 1.0, ["Meat", "Lemon"]) == TRUTH[name]["potions"]


@pytest.mark.parametrize("name,counts", [("session_01", {"Meat": 300, "Lemon": 400}),
                                         ("session_09", {"Meat": 298, "Lemon": 400}),
                                         ("session_11", {"Meat": 296, "Lemon": 398})])
def test_counts_on_older_session_frames(name, counts):
    # Not used for template extraction (counts read by eye from the frames).
    assert read_potion_counts(load(name), 1.0, ["Meat", "Lemon"]) == counts


def test_counts_on_live_keys_1_and_2():
    # Live run 2026-11-06: Meat and Lemon moved to keys 1 and 2; the "1" of Lemon's 184 sits
    # on a light key background and scored under the bar until the 1_4 variant was added.
    assert read_potion_counts(load("meso_live_keys12"), 1.0, ["Meat", "Lemon"]) == {"Meat": 25, "Lemon": 184}


def test_duplicate_names_counted_once():
    assert read_potion_counts(load("meso_full_01"), 1.0, ["Meat", "Meat"]) == {"Meat": 90}


def test_only_requested_names_returned():
    assert read_potion_counts(load("meso_full_01"), 1.0, ["Lemon"]) == {"Lemon": 272}


def test_unknown_name_is_ignored():
    assert read_potion_counts(load("meso_full_01"), 1.0, ["Meat", "Elixir"]) == {"Meat": 90}


def test_scaled_frame():
    img = load("meso_full_11")
    small = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_AREA)
    assert read_potion_counts(small, 0.75, ["Meat", "Lemon"]) == {"Meat": 89, "Lemon": 266}


def test_grid_outside_bottom_right_quarter_not_found():
    img = load("meso_full_01")
    out = np.zeros_like(img)
    out[0:720, 0:1280] = img[720:1440, 1280:2560]  # grid moved into the top-left quarter
    assert read_potion_counts(out, 1.0, ["Meat", "Lemon"]) == {}


def test_blank_frame_gives_no_counts():
    assert read_potion_counts(np.zeros((1440, 2560, 3), np.uint8), 1.0, ["Meat"]) == {}


def test_one_frame_misread_not_used():
    c = CountConfirmer()
    assert c.feed({"Meat": 107}) == {}
    assert c.feed({"Meat": 107}) == {"Meat": 107}
    assert c.feed({"Meat": 1070}) == {}
    assert c.feed({"Meat": 106}) == {}
    assert c.feed({"Meat": 106}) == {"Meat": 106}


def test_confirmer_tracks_potions_independently():
    c = CountConfirmer()
    c.feed({"Meat": 10, "Lemon": 5})
    assert c.feed({"Meat": 10}) == {"Meat": 10}
    assert c.feed({"Meat": 10, "Lemon": 5}) == {"Meat": 10}  # Lemon missed a frame in between
    assert c.feed({"Meat": 10, "Lemon": 5}) == {"Meat": 10, "Lemon": 5}


def test_same_shape_vials_told_apart_by_colour():
    # live capture, bottom-right quarter: Speed (red, PgUp), Magic (blue, 4), Sniper (pink, Del)
    # are one vial shape; in grey Magic matched Sniper's key at 0.98. Orange shows 192.
    # Speed shows "10" on the key's white background: with 8-connected blobs both digits
    # leaked into the background through a diagonal outline gap and it read "1", so a
    # count passing 11 -> 10 was charged as 10 potions drunk at once.
    frame = np.zeros((1440, 2560, 3), np.uint8)
    frame[720:, 1280:] = load("live_vials_br")
    got = read_potion_counts(frame, 1.0, ["Meat", "Lemon", "Orange Potion", "Magic Potion",
                                          "Sniper Potion", "Speed Potion"])
    assert got == {"Meat": 263, "Lemon": 358, "Orange Potion": 192, "Magic Potion": 4,
                   "Sniper Potion": 12, "Speed Potion": 10}
