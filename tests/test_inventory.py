import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from expstrip.vision.inventory import (MesosConfirmer, field_rect, find_anchor,
                                       read_inventory_mesos)

FIX = Path(__file__).parent / "fixtures"
TRUTH = json.loads((FIX / "meso_truth.json").read_text(encoding="utf-8"))
MESO_FIXTURES = sorted(TRUTH)
FIRST_WITH_INVENTORY = "meso_full_01"
FIRST_WITHOUT_INVENTORY = "session_01"


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(FIX / f"{name}.png"))
    assert img is not None, name
    return img


def shift_inventory(frame, dx, dy):
    """Cut the window (the part around the meso field) and paste it elsewhere on the same frame."""
    m = find_anchor(frame, 1.0)
    x0, y0, x1, y1 = m.x - 330, m.y - 380, m.x + 100, m.y + 60   # whole window bottom
    patch = frame[y0:y1, x0:x1].copy()
    out = frame.copy()
    out[y0 + dy:y1 + dy, x0 + dx:x1 + dx] = patch
    return out


@pytest.mark.parametrize("name", [n for n in MESO_FIXTURES if TRUTH[n]["mesos"] is not None])
def test_mesos_read(name):
    assert read_inventory_mesos(load(name), 1.0) == TRUTH[name]["mesos"]


def test_tooltip_covered_field_gives_none():
    assert TRUTH["meso_full_31"]["mesos"] is None
    assert read_inventory_mesos(load("meso_full_31"), 1.0) is None


def test_inventory_anywhere():
    f = load(FIRST_WITH_INVENTORY)
    moved = shift_inventory(f, dx=600, dy=400)
    assert not np.array_equal(moved, f)
    assert read_inventory_mesos(moved, 1.0) == TRUTH[FIRST_WITH_INVENTORY]["mesos"]


@pytest.mark.parametrize("name", ["session_01", "session_06", "session_11"])
def test_no_inventory_gives_none(name):
    assert read_inventory_mesos(load(name), 1.0) is None


def test_degenerate_frames_give_none():
    assert read_inventory_mesos(np.zeros((10, 10, 3), np.uint8), 1.0) is None
    assert read_inventory_mesos(np.zeros((1440, 2560, 3), np.uint8), 1.0) is None


def test_field_covered_by_blank_patch_gives_none():
    f = load(FIRST_WITH_INVENTORY).copy()
    m = find_anchor(f, 1.0)
    r = field_rect(m.x, m.y, 1.0)
    f[r.y:r.y + r.h, r.x + 100:r.x + r.w] = (90, 80, 70)   # a window over part of the field
    assert read_inventory_mesos(f, 1.0) is None


def test_confirmer_needs_two_equal_reads():
    c = MesosConfirmer()
    assert c.feed(100) is None
    assert c.feed(100) == 100
    assert c.feed(100) == 100
    assert c.feed(101) is None
    assert c.feed(100) is None
    assert c.feed(100) == 100


def test_confirmer_none_resets():
    c = MesosConfirmer()
    c.feed(5)
    assert c.feed(None) is None
    assert c.feed(5) is None
    assert c.feed(5) == 5
