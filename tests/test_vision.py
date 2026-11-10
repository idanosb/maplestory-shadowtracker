import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from expstrip.vision import anchors, glyphs
from expstrip.vision.layout import locate

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
TRUTH = json.loads((FIX / "truth.json").read_text(encoding="utf-8"))
ALL_FIXTURES = sorted(TRUTH)
EXP_FIXTURES = [n for n in ALL_FIXTURES if TRUTH[n]["exp_text"] is not None]
LV_FIXTURES = [n for n in ALL_FIXTURES if TRUTH[n]["level"] is not None]
FIXTURES = [n for n in EXP_FIXTURES if n in LV_FIXTURES]
NEGATIVE = [n for n in ALL_FIXTURES if n not in FIXTURES]

# Native (2560x1440) crop boxes x, y, w, h, measured on the fixtures. Task 8's layout.locate
# derives these from the anchors; here they let the reader be tested without it.
EXP_BOX = (1402, 1384, 168, 19)
LV_BOX = (612, 1390, 52, 33)


_cache: dict[str, np.ndarray] = {}


def load(name: str) -> np.ndarray:
    if name not in _cache:
        _cache[name] = cv2.imread(str(FIX / f"{name}.png"))
    return _cache[name]


def template(fname: str) -> np.ndarray:
    return cv2.imread(str(glyphs.templates_dir() / fname))


def crop(frame, box, scale=1.0):
    x, y, w, h = (round(v * scale) for v in box)
    return frame[y:y + h, x:x + w]


EXP_GS = glyphs.GlyphSet.load(glyphs.templates_dir() / "exp")
LV_GS = glyphs.GlyphSet.load(glyphs.templates_dir() / "level")


# ---------------------------------------------------------------- brief tests

@pytest.mark.parametrize("name", FIXTURES)
def test_exp_anchor_found(name):
    assert anchors.find(load(name), template("anchor_exp.png")) is not None


@pytest.mark.parametrize("name", FIXTURES)
def test_reads_exp_and_level(name):
    frame, truth = load(name), TRUTH[name]
    regions = locate(frame)
    assert glyphs.read(crop(frame, regions.exp), EXP_GS, regions.scale, "exp").replace(" ", "") == truth["exp_text"].replace(" ", "")
    assert glyphs.read(crop(frame, regions.level), LV_GS, regions.scale, "level") == str(truth["level"])


def test_reads_scaled_down_reference():
    frame = cv2.imread(str(ROOT / "docs/reference/game-screenshot-kerning.webp"))  # ~0.78x native
    m = anchors.find(frame, template("anchor_exp.png"))
    assert m is not None and 0.7 < m.scale < 0.9


# ------------------------------------------------------------ extra coverage

def _rel_box(m, offset, box):
    dx, dy = offset
    return (m.x + round(dx * m.scale), m.y + round(dy * m.scale), round(box[2] * m.scale), round(box[3] * m.scale))


def test_reads_text_in_scaled_down_reference():
    # The lossy 0.78x reference shows level 44 and "114,698 [26.78%]".
    frame = cv2.imread(str(ROOT / "docs/reference/game-screenshot-kerning.webp"))
    me = anchors.find(frame, template("anchor_exp.png"))
    ml = anchors.find(frame, template("anchor_lv.png"))
    assert me is not None and ml is not None
    exp_box = _rel_box(me, (EXP_BOX[0] - 1352, EXP_BOX[1] - 1380), EXP_BOX)
    lv_box = _rel_box(ml, (LV_BOX[0] - 553, LV_BOX[1] - 1392), LV_BOX)
    assert glyphs.read(crop(frame, exp_box), EXP_GS, me.scale, "exp") == "114,698[26.78%]"
    assert glyphs.read(crop(frame, lv_box), LV_GS, ml.scale, "level") == "44"


def test_glyphset_inventory():
    assert set("0123456789,.%[]") <= set(EXP_GS.chars)
    assert "4" in LV_GS.chars


def test_glyphset_alias_names(tmp_path):
    for stem in ["comma", "dot", "pct", "lbr", "rbr", "7"]:
        cv2.imwrite(str(tmp_path / f"{stem}.png"), np.full((5, 3), 255, np.uint8))
    assert sorted(glyphs.GlyphSet.load(tmp_path).chars) == sorted(",.%[]7")


def test_exp_anchor_location_and_scale_native():
    m = anchors.find(load("session_06"), template("anchor_exp.png"))
    assert m is not None
    assert abs(m.scale - 1.0) < 0.03 and m.score > 0.95
    assert abs(m.x - 1352) <= 2 and abs(m.y - 1380) <= 2


@pytest.mark.parametrize("name", FIXTURES)
def test_lv_anchor_found(name):
    m = anchors.find(load(name), template("anchor_lv.png"))
    assert m is not None and abs(m.scale - 1.0) < 0.03


def test_find_returns_none_when_absent():
    blank = np.zeros((300, 400, 3), np.uint8)
    assert anchors.find(blank, template("anchor_exp.png")) is None


@pytest.mark.parametrize("name", EXP_FIXTURES)
def test_reads_exp_native_crop(name):
    text = glyphs.read(crop(load(name), EXP_BOX), EXP_GS, 1.0, "exp")
    assert text == TRUTH[name]["exp_text"].replace(" ", "")


@pytest.mark.parametrize("name", FIXTURES)
def test_reads_level_native_crop(name):
    assert glyphs.read(crop(load(name), LV_BOX), LV_GS, 1.0, "level") == str(TRUTH[name]["level"])


@pytest.mark.parametrize("scale", [0.78, 1.25])
def test_reads_rescaled_frame(scale):
    frame = cv2.resize(load("session_06"), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    m = anchors.find(frame, template("anchor_exp.png"))
    assert m is not None and abs(m.scale - scale) < 0.06
    assert glyphs.read(crop(frame, EXP_BOX, scale), EXP_GS, m.scale, "exp") == TRUTH["session_06"]["exp_text"].replace(" ", "")
    assert glyphs.read(crop(frame, LV_BOX, scale), LV_GS, m.scale, "level") == "44"


def test_level_unknown_digit_is_question_mark():
    # Only "4" exists in the level templates; a tile holding a different digit must not read as "4".
    tile = np.zeros((33, 30, 3), np.uint8)
    tile[4:30, 3:27] = (0x3d, 0x7a, 0xff)  # orange BGR
    cv2.putText(tile, "1", (9, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    assert glyphs.read(tile, LV_GS, 1.0, "level") == "?"


def test_read_empty_crop_returns_empty_string():
    assert glyphs.read(np.zeros((19, 100, 3), np.uint8), EXP_GS, 1.0, "exp") == ""


@pytest.mark.parametrize("name", NEGATIVE)
def test_negative_fixture_has_no_valid_reading(name):
    # Light check only; Task 8 owns the full negative case. In session_01 the taskbar hides
    # the lower part of the status bar: the level tiles are covered (no LV anchor, no level
    # digits), while the EXP text line stays visible (it is read in test_reads_exp_native_crop).
    frame = load(name)
    lv = anchors.find(frame, template("anchor_lv.png"))
    level_text = glyphs.read(crop(frame, LV_BOX), LV_GS, 1.0, "level")
    assert lv is None and not level_text.isdigit()


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_minimap_anchors(name):
    frame = load(name)[0:480, 0:960]
    world = anchors.find(frame, template("anchor_minimap_world.png"))
    assert world is not None
    expanded = anchors.find(frame, template("anchor_minimap_expanded.png"))
    collapsed = anchors.find(frame, template("anchor_minimap_collapsed.png"), min_score=0.95)
    if TRUTH[name].get("minimap") == "collapsed":
        assert expanded is None and collapsed is not None
    elif TRUTH[name].get("minimap") == "expanded":
        assert expanded is not None and collapsed is None


# ------------------------------------------------------------ fix round 1

def _pad_to(img, h, w):
    out = np.zeros((h, w), np.float32)
    out[:img.shape[0], :img.shape[1]] = img
    return out


@pytest.mark.parametrize("a, b", [("6", "8"), ("8", "9"), ("3", "8"), ("3", "9")])
def test_blended_digit_is_rejected(a, b):
    # A 50/50 blend of two confusable digits must read "?", never a confident wrong digit.
    ta, tb = EXP_GS.templates[a][0], EXP_GS.templates[b][0]
    h, w = max(ta.shape[0], tb.shape[0]), max(ta.shape[1], tb.shape[1])
    blend = 0.5 * _pad_to(ta, h, w) + 0.5 * _pad_to(tb, h, w)
    assert glyphs.classify(blend, EXP_GS)[0] == "?"


def test_clean_digits_still_accepted():
    for ch in "0123456789":
        assert glyphs.classify(EXP_GS.templates[ch][0], EXP_GS) == (ch, pytest.approx(1.0, abs=1e-4))


def test_stray_pixels_above_text_do_not_break_reading():
    c = crop(load("session_06"), EXP_BOX).copy()
    c[0, 10] = c[0, 100] = (255, 255, 255)  # two isolated bright pixels on the top row
    assert glyphs.read(c, EXP_GS, 1.0, "exp") == "115,051[26.86%]"


def test_stray_pixel_pair_and_border_below_text():
    c = load("session_06")[1380:1409, 1402:1570].copy()  # taller crop that reaches the EXP bar border
    c[0, 40:42] = (255, 255, 255)
    assert glyphs.read(c, EXP_GS, 1.0, "exp") == "115,051[26.86%]"


@pytest.fixture(scope="module")
def extract_tool():
    import sys
    tools = str(ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import extract_templates
    return extract_templates


@pytest.mark.parametrize("held", FIXTURES)
def test_leave_one_fixture_out(held, tmp_path, extract_tool):
    # Templates rebuilt from the *other* fixtures only, so this is not circular.
    for name in FIXTURES:
        if name == held:
            continue
        frame = load(name)
        extract_tool.extract_glyphs(frame, EXP_BOX, TRUTH[name]["exp_text"], "exp", out_dir=tmp_path / "exp")
        extract_tool.extract_glyphs(frame, LV_BOX, str(TRUTH[name]["level"]), "level", out_dir=tmp_path / "level")
    exp_gs = glyphs.GlyphSet.load(tmp_path / "exp")
    lv_gs = glyphs.GlyphSet.load(tmp_path / "level")
    want_exp = TRUTH[held]["exp_text"].replace(" ", "")
    want_lv = str(TRUTH[held]["level"])

    frame = load(held)
    assert glyphs.read(crop(frame, EXP_BOX), exp_gs, 1.0, "exp") == want_exp
    assert glyphs.read(crop(frame, LV_BOX), lv_gs, 1.0, "level") == want_lv

    small = cv2.resize(frame, None, fx=0.78, fy=0.78, interpolation=cv2.INTER_AREA)
    assert glyphs.read(crop(small, EXP_BOX, 0.78), exp_gs, 0.80, "exp") == want_exp
    assert glyphs.read(crop(small, LV_BOX, 0.78), lv_gs, 0.80, "level") == want_lv


# ------------------------------------------------------------ Task 8: layout.locate

@pytest.mark.parametrize("name", FIXTURES)
def test_locate_finds_status_bar(name):
    r = locate(load(name))
    assert r is not None and r.exp.w > 0
    assert abs(r.scale - 1.0) < 0.03
    assert abs(r.exp.x - EXP_BOX[0]) <= 2 and abs(r.exp.y - EXP_BOX[1]) <= 2
    assert abs(r.level.x - LV_BOX[0]) <= 2 and abs(r.level.y - LV_BOX[1]) <= 2


def test_locate_taskbar_hides_level():
    r = locate(load("session_01"))
    assert r is not None and r.level is None and r.exp.w > 0


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_locate_minimap_form(name):
    r = locate(load(name))
    form = TRUTH[name].get("minimap", "expanded")  # session_01 shows the expanded minimap
    assert r.minimap_form == form and r.minimap_title is not None
    t = r.minimap_title
    if form == "collapsed":  # one line in the light bar, left of the -/+ buttons
        assert t.y < 15 and 18 <= t.h <= 30 and t.x + t.w <= 431
    else:  # two lines right of the map icon, under the MINI MAP header
        assert t.x >= 82 and 40 <= t.y <= 55 and t.h >= 55


def test_locate_none_on_blank():
    assert locate(np.zeros((1440, 2560, 3), np.uint8)) is None


def test_locate_scaled_letterboxed_reference():
    frame = cv2.imread(str(ROOT / "docs/reference/game-screenshot-kerning.webp"))  # ~0.78x, collapsed minimap
    r = locate(frame)
    assert r is not None and 0.7 < r.scale < 0.9
    assert glyphs.read(crop(frame, r.exp), EXP_GS, r.scale, "exp") == "114,698[26.78%]"
    assert glyphs.read(crop(frame, r.level), LV_GS, r.scale, "level") == "44"
    assert r.minimap_form == "collapsed" and r.minimap_title is not None
    assert r.minimap_title.y < 15 and r.minimap_title.h < 30


def test_single_digit_9_pct_reads():
    # Live crop (2026-11-07, scale 1.0): the 9 right after "[" scored 0.91 vs 0.89 for 8 with
    # only the white 9 template, so the whole 9.xx% range read "?" and the strip said
    # "Can't read EXP". The 9_2 variant was cut from this crop.
    gs = glyphs.GlyphSet.load(glyphs.templates_dir() / "exp")
    crop = cv2.imread(str(FIX / "exp_pct_9_28.png"))
    assert glyphs.read(crop, gs, 1.0, "exp") == "47,384[9.28%]"


@pytest.mark.parametrize("size", [(3840, 2160), (1920, 1080), (1366, 768)])
def test_locate_reads_common_resolutions(size):
    """The UI scale comes from the LV-EXP label distance (snapped to client height / 1440), so
    the readers get it exactly; a 0.05-step template scale misread digits below 1920x1080."""
    w, h = size
    src = load("session_06")
    frame = cv2.resize(src, size, interpolation=cv2.INTER_AREA if w < src.shape[1] else cv2.INTER_LINEAR)
    r = locate(frame)
    assert r is not None and abs(r.scale - h / 1440) < 1e-9
    assert glyphs.read(r.exp.crop(frame), EXP_GS, r.scale, "exp") == TRUTH["session_06"]["exp_text"].replace(" ", "")
    assert glyphs.read(r.level.crop(frame), LV_GS, r.scale, "level") == "44"
