import numpy as np

from expstrip.vision import items
from expstrip.vision.item_names import LEARN_AFTER, NameLearner, match_ocr

NAMES = ["Tablecloth", "Stirge Wing", "Orange Potion", "White Potion", "Animal Fur Bundle"]


def test_exact_match_ignores_case_and_spaces():
    assert match_ocr("Orange  potion", NAMES) == "Orange Potion"
    assert match_ocr(" Tablecloth ", NAMES) == "Tablecloth"


def test_vv_read_as_w():
    assert match_ocr("Stirge VVing", NAMES) == "Stirge Wing"


def test_noise_and_partial_reads_do_not_match():
    for text in ("", "Tabiecicth", "Stirge", "Orange P vtlo_rt", "Iron Ore"):
        assert match_ocr(text, NAMES) is None


def test_cut_name_matches_unique_start():
    assert match_ocr("Animal Fur Bun...", NAMES) == "Animal Fur Bundle"
    assert match_ocr("Anim...", NAMES) is None  # too short to trust


def _strip():
    s = np.zeros((items.LINE_H, 40), np.float32)
    s[5:15, 3:30] = 1.0
    return s


def test_learner_saves_template_after_repeated_reads(tmp_path):
    learner = NameLearner(tmp_path, ocr=lambda img: ["White Potion"])
    try:
        for i in range(LEARN_AFTER):
            assert learner(_strip(), NAMES) == "White Potion"
            assert (tmp_path / "White Potion.png").exists() == (i == LEARN_AFTER - 1)
        assert items.has_name_template("White Potion")
        assert learner.learned == ["White Potion"]
    finally:
        items._EXTRA_DIRS.remove(tmp_path)
        items.reload_templates()


def test_learner_ignores_names_not_in_price_list(tmp_path):
    learner = NameLearner(tmp_path, ocr=lambda img: ["Orihalcon Ore"])
    try:
        for _ in range(LEARN_AFTER + 1):
            assert learner(_strip(), NAMES) is None
        assert not list(tmp_path.glob("*.png"))
    finally:
        items._EXTRA_DIRS.remove(tmp_path)
        items.reload_templates()


def test_ocr_failure_is_not_fatal(tmp_path):
    def boom(img):
        raise RuntimeError("no OCR")
    learner = NameLearner(None, ocr=boom)
    assert learner(_strip(), NAMES) is None
