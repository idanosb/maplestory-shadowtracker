import numpy as np

from expstrip.icon_learner import CONFIRM, PENDING_S, IconLearner
from expstrip.vision import quickslots
from expstrip.vision.items import ItemLine

POTIONS = ["White Potion", "Meat"]
FRAME = np.zeros((1440, 2560, 3), np.uint8)


def _icon(seed=1):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (quickslots.ICON_H, quickslots.ICON_W, 3), dtype=np.uint8)


class Fake:
    def __init__(self):
        self.counts = {4: 90, 5: 271, 6: 10}
        self.icon = _icon()

    def read(self, frame, scale, origins):
        return dict(self.counts)

    def cut(self, frame, scale, origin):
        return self.icon


def _learner(tmp_path, fake):
    return IconLearner(tmp_path, read_counts=fake.read, cut_icon=fake.cut)


def _pickup(learner, fake, key, qty, t):
    learner.on_scan(FRAME, 1.0, t)
    learner.on_items([ItemLine("White Potion", qty, "Use")], POTIONS, t + 0.5)
    fake.counts[key] += qty
    return learner.on_scan(FRAME, 1.0, t + 5)


def _cleanup(tmp_path):
    quickslots._EXTRA_ICON_DIRS.remove(tmp_path)
    quickslots.reload_icons()


def test_icon_learned_after_confirmed_pickups(tmp_path):
    fake = Fake()
    learner = _learner(tmp_path, fake)
    try:
        for i in range(CONFIRM):
            got = _pickup(learner, fake, 6, 2, i * 10.0)
            assert got == (["White Potion"] if i == CONFIRM - 1 else [])
        assert (tmp_path / "White Potion.png").exists()
        assert quickslots.has_icon("White Potion")
    finally:
        _cleanup(tmp_path)


def test_two_keys_rising_teaches_nothing(tmp_path):
    fake = Fake()
    learner = _learner(tmp_path, fake)
    try:
        for i in range(CONFIRM + 1):
            learner.on_scan(FRAME, 1.0, i * 10.0)
            learner.on_items([ItemLine("White Potion", 1, "Use")], POTIONS, i * 10.0 + 0.5)
            fake.counts[5] += 1
            fake.counts[6] += 1
            assert learner.on_scan(FRAME, 1.0, i * 10.0 + 5) == []
        assert not list(tmp_path.glob("*.png"))
    finally:
        _cleanup(tmp_path)


def test_different_keys_do_not_confirm(tmp_path):
    fake = Fake()
    learner = _learner(tmp_path, fake)
    try:
        assert _pickup(learner, fake, 6, 1, 0.0) == []
        assert _pickup(learner, fake, 5, 1, 10.0) == []
        assert not list(tmp_path.glob("*.png"))
    finally:
        _cleanup(tmp_path)


def test_pending_pickup_expires(tmp_path):
    fake = Fake()
    learner = _learner(tmp_path, fake)
    try:
        learner.on_scan(FRAME, 1.0, 0.0)
        learner.on_items([ItemLine("White Potion", 3, "Use")], POTIONS, 0.5)
        learner.on_scan(FRAME, 1.0, PENDING_S + 5)  # nothing rose: dropped
        assert learner._pending == {}
    finally:
        _cleanup(tmp_path)


def test_etc_lines_and_known_icons_ignored(tmp_path):
    fake = Fake()
    learner = _learner(tmp_path, fake)
    try:
        learner.on_items([ItemLine("White Potion", 1, "Etc"), ItemLine("Meat", 1, "Use"),
                          ItemLine("Tablecloth", 1, "Use")], POTIONS, 0.0)
        assert learner._pending == {}  # Meat already has a bundled icon; Tablecloth no potion
    finally:
        _cleanup(tmp_path)


def test_key_grid_on_recording_matches_bundled_icon():
    import cv2
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    f = cv2.imread(str(root / "recordings" / "meso_b" / "full_0002.png"))
    if f is None:
        import pytest
        pytest.skip("recording not present")
    origins = quickslots.key_origins(f, 1.0)
    assert quickslots.read_key_counts(f, 1.0, origins) == {4: 90, 5: 271, 6: 84, 7: 8}
    cut = cv2.cvtColor(quickslots.key_icon(f, 1.0, origins[4]), cv2.COLOR_BGR2GRAY)
    meat = quickslots._icons()["Meat"]
    assert float(cv2.matchTemplate(cut, meat, cv2.TM_CCOEFF_NORMED)[0, 0]) > 0.95
