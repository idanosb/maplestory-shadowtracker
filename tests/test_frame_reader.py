import json
import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

from expstrip import frame_reader as fr_mod
from expstrip.config import Settings, load_settings, save_settings
from expstrip.frame_reader import FrameReader, infer_level, is_level_up_drop, snap_map_name
from expstrip.reading import MapName, normalize_map_name, parse_exp_text
from expstrip.vision import glyphs

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
TRUTH = json.loads((FIX / "truth.json").read_text(encoding="utf-8"))
ALL_FIXTURES = sorted(TRUTH)

_cache: dict[str, np.ndarray] = {}


def load(name: str) -> np.ndarray:
    if name not in _cache:
        _cache[name] = cv2.imread(str(FIX / f"{name}.png"))
    return _cache[name]


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# ------------------------------------------------------------------ brief tests

@pytest.mark.parametrize("name", [n for n in ALL_FIXTURES if TRUTH[n]["map_full"]])
def test_frame_reader_full(name):
    level, ep, m = FrameReader().read(load(name))
    t = TRUTH[name]
    assert level == t["level"] and ep == parse_exp_text(t["exp_text"]) and m.full == t["map_full"]


def test_frame_reader_on_blank_frame():
    assert FrameReader().read(np.zeros((1440, 2560, 3), np.uint8)) == (None, None, None)


# ------------------------------------------------------------ taskbar (ruling A.4)

def test_taskbar_fresh_reader_gives_none_despite_hint():
    level, ep, _ = FrameReader(level_hint=50).read(load("session_01"))
    assert level is None and ep == parse_exp_text(TRUTH["session_01"]["exp_text"])


def test_taskbar_appearing_with_cached_regions_gives_none():
    clock = Clock()
    r = FrameReader(clock=clock)
    assert r.read(load("session_02"))[0] == 44  # level known, regions cached (same frame size)
    clock.t += 1
    level, ep, _ = r.read(load("session_01"))  # taskbar now covers the LV label: no inference
    assert level is None and ep == parse_exp_text(TRUTH["session_01"]["exp_text"])
    clock.t += 1
    assert r.read(load("session_02"))[0] == 44  # LV label back


def test_started_with_taskbar_finds_level_once_it_is_visible():
    """Regression (live run): started while the taskbar hid the LV label, the reader cached
    regions without a level and, since EXP kept parsing, never looked for it again."""
    clock = Clock()
    r = FrameReader(clock=clock)
    assert r.read(load("session_01"))[0] is None  # taskbar covers the LV label at startup
    clock.t += 1
    assert r.read(load("session_02"))[0] is None  # not re-checked every frame (cheap path)
    clock.t += fr_mod.MINIMAP_INTERVAL
    assert r.read(load("session_02"))[0] == 44   # periodic re-locate finds the level


# ------------------------------------------------------------ level inference (ruling A)

def test_is_level_up_drop():
    assert is_level_up_drop((500, 0.5), (230000, 99.1))
    assert not is_level_up_drop((231000, 9.12), (230000, 99.12))  # pct misread: EXP not down
    assert not is_level_up_drop((200000, 60.0), (230000, 99.0))   # dropped only 39 points
    assert not is_level_up_drop(None, (230000, 99.0)) and not is_level_up_drop((5, 0.5), None)


def test_infer_level_normal_read():
    assert infer_level("44", None, 0, None) == (44, False)
    assert infer_level("45", 44, 2, None) == (45, False)


def test_infer_level_level_up_needs_two_drop_frames():
    assert infer_level("4?", 44, 1, None) == (44, False)  # first drop frame: not yet
    assert infer_level("4?", 44, 2, None) == (45, True)
    assert infer_level("", 44, 2, None) == (45, True)


def test_infer_level_keeps_last_level_without_drop():
    assert infer_level("??", 44, 0, None) == (44, False)


def test_infer_level_uses_hint_without_history():
    assert infer_level("??", None, 0, 37) == (37, False)
    assert infer_level("??", None, 0, None) == (None, False)


def _stub_reader(raws, eps, **kw):
    r = FrameReader(**kw)
    raws, eps = iter(raws), iter(eps)
    r._read_level_raw = lambda frame, regions: next(raws)
    r._read_exp = lambda frame, regions: next(eps)
    r._map_name = lambda frame, now: None
    return r


def test_reader_level_up_sequence_and_hint_once():
    r = _stub_reader(["??"] * 5, [(229000, 98.5), (230000, 99.4), (500, 0.5), (700, 0.7), (900, 0.9)],
                     level_hint=44)
    frame = load("session_02")
    got = [r.read(frame)[:2] for _ in range(5)]
    assert [lv for lv, _ in got] == [44, 44, 44, 45, 45]
    #  hint used ^, kept ^, first drop frame ^, confirmed ^
    # the unconfirmed drop frame hides its EXP so the old level is never paired with it
    assert [ep for _, ep in got] == [(229000, 98.5), (230000, 99.4), None, (700, 0.7), (900, 0.9)]


@pytest.mark.parametrize("raws, eps", [
    # "99.12%" misread as "9.12%" on one frame, then read correctly again
    (["??"] * 3, [(230000, 99.10), (230100, 9.12), (230200, 99.13)]),
    # pct misread repeated, but the absolute EXP did not drop
    (["??"] * 3, [(230000, 99.10), (230100, 9.12), (230200, 9.13)]),
    # a real-looking drop on one frame only
    (["??"] * 3, [(230000, 99.10), (500, 0.5), (230200, 99.13)]),
])
def test_single_misread_frame_does_not_change_level(tmp_path, raws, eps):
    learned = tmp_path / "learned"
    r = _stub_reader(raws, eps, level_hint=44, learned_dir=learned)
    frame = load("session_02")
    got = [r.read(frame) for _ in range(3)]
    assert [g[0] for g in got] == [44, 44, 44]
    assert got[2][1] == eps[2]  # the frame after a pending drop reports its EXP again
    assert not learned.exists()


def _learning_reader(tmp_path, last_level, eps):
    # Bundled level templates are replaced by an empty folder, so the real "44" tiles read "".
    empty = tmp_path / "bundled"
    empty.mkdir()
    learned = tmp_path / "learned"
    r = FrameReader(level_templates=empty, learned_dir=learned)
    r._last_level, r._last_ep = last_level, (230000, 99.2)
    eps = iter(eps)
    real_exp = r._read_exp
    r._read_exp = lambda frame, regions: next(eps, None) or real_exp(frame, regions)
    return r, empty, learned


def test_level_glyph_learning_after_confirmation(tmp_path):
    r, empty, learned = _learning_reader(tmp_path, 43, [])
    frame = load("session_06")  # Lv 44, 115,051 [26.86%]
    level, ep, _ = r.read(frame)
    assert level == 43 and ep is None             # first drop frame: unconfirmed, EXP withheld
    level, ep, _ = r.read(frame)
    assert level == 44 and ep == (115051, 26.86)  # second: level-up; glyph frame 1
    assert r.read(frame)[0] == 44                 # glyph frame 2
    assert not learned.exists()                   # nothing written or used yet
    assert r._read_level_raw(frame, r._regions) == ""
    assert r.read(frame)[0] == 44                 # glyph frame 3: confirmed
    assert sorted(p.name for p in learned.iterdir()) == ["4.png"]
    assert r._read_level_raw(frame, r._regions) == "44"  # merged into the GlyphSet
    r2 = FrameReader(level_templates=empty, learned_dir=learned)  # bundled + learned at load
    assert r2.read(frame)[0] == 44 and r2._last_level == 44


def test_glyph_learning_needs_consecutive_agreeing_frames(tmp_path):
    r, _, learned = _learning_reader(tmp_path, 43, [])
    frame = load("session_06")
    other = load("session_06").copy()
    r.read(frame)
    r.read(frame)  # level-up confirmed; glyph frame 1
    r.read(frame)  # glyph frame 2
    lv = r._regions.level
    tile = other[lv.y:lv.y + lv.h, lv.x:lv.x + lv.w]
    tile[:, : lv.w // 2] = cv2.flip(tile[:, : lv.w // 2], 0)  # first glyph upside down: disagrees
    r.read(other)  # restarts the capture at 1
    r.read(frame)  # 1 (disagrees with the flipped capture)
    r.read(frame)  # 2
    assert not learned.exists()
    r.read(frame)  # 3
    assert (learned / "4.png").exists()


def test_learning_skipped_when_glyph_count_mismatches(tmp_path):
    r, _, learned = _learning_reader(tmp_path, 99, [])  # level 100: 3 digits vs 2 tile glyphs
    frame = load("session_06")
    assert [r.read(frame)[0] for _ in range(5)] == [99, 100, 100, 100, 100]
    assert not learned.exists()


def test_glyphset_load_many(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    cv2.imwrite(str(a / "4.png"), np.full((5, 3), 255, np.uint8))
    cv2.imwrite(str(b / "5.png"), np.full((5, 3), 255, np.uint8))
    gs = glyphs.GlyphSet.load_many([a, b, tmp_path / "missing"])
    assert sorted(gs.chars) == ["4", "5"]


# ------------------------------------------------------------ map names (ruling B)

def test_snap_map_name():
    true = MapName("Kerning City Subway", "Line 1 <Area 2>")
    known = {true.full: true}
    got = snap_map_name(MapName("Kerning City Subvvay", "Line 1 <Area 2>"), known)
    assert got == true
    other = MapName("Victoria Road", "Kerning City")
    assert snap_map_name(other, known) == other  # far from every known name: kept


def test_snap_prefers_area_digit_mismatch_not_merged():
    # "<Area 1>" vs "<Area 2>" differ by one char; ratio > 0.85, so it must still snap only to
    # the closest known name, which here is itself once both are known.
    a1 = MapName("Kerning City Subway", "Line 1 <Area 1>")
    a2 = MapName("Kerning City Subway", "Line 1 <Area 2>")
    known = {a1.full: a1, a2.full: a2}
    assert snap_map_name(a1, known) == a1 and snap_map_name(a2, known) == a2


def test_known_maps_param_snaps_ocr_result():
    r = FrameReader(known_maps=["Kerning City Subway : Line 1 <Area 2>"])
    r._ocr = lambda img: ["Kerning City Subvvay : Line 1 <Area 2>"]
    _, _, m = r.read(load("session_06"))
    assert m == MapName("Kerning City Subway", "Line 1 <Area 2>")


# ------------------------------------------------------------ caching / throttling

def test_regions_cached_and_relocated_on_size_change(monkeypatch):
    calls = []
    real = fr_mod.layout.locate
    monkeypatch.setattr(fr_mod.layout, "locate", lambda f: calls.append(f.shape) or real(f))
    clock = Clock()
    r = FrameReader(clock=clock)
    frame = load("session_06")
    r.read(frame)
    clock.t += 1
    r.read(frame)
    assert len(calls) == 1
    small = cv2.resize(frame, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
    clock.t += 1
    level, ep, _ = r.read(small)
    assert len(calls) == 2 and level == 44 and ep == (115051, 26.86)


def test_relocates_after_five_failed_exp_parses(monkeypatch):
    calls = []
    real = fr_mod.layout.locate
    monkeypatch.setattr(fr_mod.layout, "locate", lambda f: calls.append(1) or real(f))
    clock = Clock()
    r = FrameReader(clock=clock)
    r.read(load("session_06"))
    junk = load("session_06").copy()
    junk[1370:1420, 1390:1600] = 0  # EXP text wiped, same frame size
    for _ in range(4):
        clock.t += 1
        r.read(junk)
    assert len(calls) == 1
    clock.t += 1
    r.read(junk)  # 5th consecutive failure
    assert len(calls) == 2


def test_ocr_throttled_to_3s_and_minimap_relocated_every_30s(monkeypatch):
    ocr_calls, mm_calls = [], []
    real_mm = fr_mod.layout.locate_minimap
    monkeypatch.setattr(fr_mod.layout, "locate_minimap", lambda f, s=None: mm_calls.append(1) or real_mm(f, s))
    clock = Clock()
    r = FrameReader(clock=clock)
    real_ocr = r._ocr
    r._ocr = lambda img: ocr_calls.append(1) or real_ocr(img)
    frame = load("session_06")
    m0 = r.read(frame)[2]
    assert len(ocr_calls) == 1
    for _ in range(2):
        clock.t += 1
        assert r.read(frame)[2] == m0  # reused between OCR runs
    assert len(ocr_calls) == 1
    clock.t += 1  # 3 s since the last OCR
    r.read(frame)
    assert len(ocr_calls) == 2
    n = len(mm_calls)
    clock.t += 31
    r.read(frame)
    assert len(mm_calls) == n + 1


# ------------------------------------------------------------ fail-safe + debug (ruling C)

def test_exception_inside_read_does_not_propagate(tmp_path):
    r = FrameReader(debug_dir=tmp_path)

    def boom(frame, regions):
        raise RuntimeError("level reader exploded")

    r._read_level_raw = boom
    level, ep, m = r.read(load("session_06"))
    assert ep == (115051, 26.86) and level is None
    log = (tmp_path / "readings.log").read_text(encoding="utf-8")
    assert "level reader exploded" in log


def test_garbage_input_returns_nones():
    r = FrameReader()
    assert r.read(None) == (None, None, None)
    assert r.read(np.zeros((0, 0, 3), np.uint8)) == (None, None, None)


def test_debug_dir_saves_crops_and_log(tmp_path):
    clock = Clock()
    r = FrameReader(debug_dir=tmp_path, clock=clock)
    r.read(load("session_06"))
    clock.t += 0.3
    r.read(load("session_06"))  # within the same second: no second dump
    names = sorted(p.name for p in tmp_path.iterdir())
    assert sum(n.endswith("_exp.png") for n in names) == 1
    assert sum(n.endswith("_level.png") for n in names) == 1
    assert sum(n.endswith("_map.png") for n in names) == 1
    log = (tmp_path / "readings.log").read_text(encoding="utf-8")
    assert "115051" in log and "Kerning City Subway" in log
    assert not any(n.endswith("_full.png") for n in names)  # EXP read: no full frame


def test_debug_saves_full_frame_when_no_exp_bar(tmp_path):
    clock = Clock()
    r = FrameReader(debug_dir=tmp_path, clock=clock)
    blank = np.full((1200, 1920, 3), 40, np.uint8)
    r.read(blank)
    clock.t += 2
    r.read(blank)  # within 10 s: no second full frame
    names = [p.name for p in tmp_path.iterdir()]
    assert sum(n.endswith("_full_unlocated.png") for n in names) == 1
    assert "EXP bar not found; frame 1920x1200" in (tmp_path / "readings.log").read_text(encoding="utf-8")


# ------------------------------------------------------------ settings

def test_settings_level_hint_roundtrip(tmp_path):
    p = tmp_path / "settings.json"
    save_settings(Settings(level_hint=37), p)
    assert load_settings(p).level_hint == 37


def test_settings_old_file_without_level_hint(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"window_title": "Maple", "strip_x": 1, "strip_y": 2, "locked": True}), encoding="utf-8")
    s = load_settings(p)
    assert s.level_hint is None and s.window_title == "Maple" and s.locked is True


def test_area_number_change_is_not_snapped():
    a1 = MapName("Kerning City Subway", "Line 1 <Area 1>")
    a2 = MapName("Kerning City Subway", "Line 1 <Area 2>")
    assert snap_map_name(a2, {a1.full: a1}) == a2
    assert snap_map_name(a1, {a2.full: a2}) == a1
    # a lookalike inside a numeric token still snaps
    l10 = MapName("Hidden Street", "Line 10")
    assert snap_map_name(MapName("Hidden Street", "Line 1O"), {l10.full: l10}) == l10


@pytest.mark.parametrize("ocr, true", [
    ("Victaria Road : Ellinia", "Victoria Road : Ellinia"),
    ("Victoria Road : Eilinia", "Victoria Road : Ellinia"),
    ("Victoria Raad : Kerning City", "Victoria Road : Kerning City"),
])
def test_letters_are_not_digits_when_snapping(ocr, true):
    t = normalize_map_name([true])
    assert snap_map_name(normalize_map_name([ocr]), {t.full: t}) == t


def test_ocr_name_becomes_known_after_two_reads():
    clock = Clock()
    r = FrameReader(clock=clock)
    reads = iter(["Victoria Raad : Kerning City",   # plausible misread, read once
                  "Victoria Road : Kerning City",   # not attracted by the single misread
                  "Victoria Road : Kerning City",   # 2nd read: now known
                  "Victoria Roab : Kerning City"])  # snaps onto the known name
    r._ocr = lambda img: [next(reads)]
    got = []
    for _ in range(4):
        clock.t += 31
        got.append(r.read(load("session_06"))[2].full)
    assert got == ["Victoria Raad : Kerning City"] + ["Victoria Road : Kerning City"] * 3
    assert set(r._known) == {"Victoria Road : Kerning City"}


def test_known_maps_are_trusted_immediately():
    r = FrameReader(known_maps=["Victoria Road : Ellinia"])
    r._ocr = lambda img: ["Victaria Road : Ellinia"]
    assert r.read(load("session_06"))[2] == MapName("Victoria Road", "Ellinia")


def test_errors_logged_at_warning_once_per_distinct_error(caplog):
    r = FrameReader()
    msgs = iter(["en-US OCR not available"] * 3 + ["other fault"])

    def boom(frame, regions):
        raise RuntimeError(next(msgs))

    r._read_level_raw = boom
    with caplog.at_level(logging.DEBUG, logger="expstrip.frame_reader"):
        for _ in range(4):
            r.read(load("session_06"))
    warns = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert len(warns) == 2
    assert "en-US OCR not available" in warns[0].getMessage() and "other fault" in warns[1].getMessage()


def test_one_reader_across_maps():
    clock = Clock()
    r = FrameReader(clock=clock)
    for name in ["session_02", "session_03", "session_04", "session_06", "session_07", "session_01"]:
        clock.t += 31  # force OCR + minimap re-locate
        assert r.read(load(name))[2].full == TRUTH[name]["map_full"], name


def test_level_up_while_tiles_hidden_is_still_detected():
    r = FrameReader()
    # readable, hidden by the taskbar, back but unreadable (2 frames to confirm)
    raws = iter(["44", None, "??", "??"])
    eps = iter([(230000, 99.3), (400, 0.4), (600, 0.6), (700, 0.7)])
    r._read_level_raw = lambda frame, regions: next(raws)
    r._read_exp = lambda frame, regions: next(eps)
    r._map_name = lambda frame, now: None
    frame = load("session_02")
    assert [r.read(frame)[:2] for _ in range(4)] == [
        (44, (230000, 99.3)), (None, (400, 0.4)), (44, None), (45, (700, 0.7))]


def test_scale_is_none_until_located_then_the_located_scale():
    r = FrameReader()
    assert r.scale is None
    r.read(load("session_01"))
    assert r.scale is not None and 0.5 < r.scale < 3


def test_hint_teaches_missing_level_digit(tmp_path):
    # the real tiles read "44"; with only an empty template folder they read "", so stub a
    # partial read "4?" (as a level-46 tile reads with only the "4" known)
    empty = tmp_path / "bundled"
    empty.mkdir()
    learned = tmp_path / "learned"
    r = FrameReader(level_templates=empty, learned_dir=learned, level_hint=44)
    r._read_level_raw = lambda frame, regions: "4?"
    frame = load("session_02")
    got = [r.read(frame)[0] for _ in range(4)]
    assert got == [44] * 4 and r.hint_used
    assert (learned / "4.png").exists()


def test_stale_hint_teaches_nothing(tmp_path):
    empty = tmp_path / "bundled"
    empty.mkdir()
    learned = tmp_path / "learned"
    r = FrameReader(level_templates=empty, learned_dir=learned, level_hint=44)
    r._read_level_raw = lambda frame, regions: "5?"   # the tile says 5x: the hint is stale
    frame = load("session_02")
    for _ in range(4):
        r.read(frame)
    assert not learned.exists()


def test_hint_fits():
    from expstrip.frame_reader import hint_fits
    assert hint_fits("4?", 46) and hint_fits("?6", 46)
    assert not hint_fits("??", 46) and not hint_fits("5?", 46) and not hint_fits("4", 46)


def test_unknown_level_digits_are_guessed_from_exp_font_then_learned(tmp_path):
    # A fresh install whose tile templates lack the player's digits (a laptop at Lv 47 had only
    # "4" bundled): the tiles read "??", the level is guessed from the EXP font after
    # GUESS_CONFIRM_FRAMES frames, then the real tile glyphs are learned from it.
    import shutil
    from expstrip.frame_reader import GLYPH_CONFIRM_FRAMES, GUESS_CONFIRM_FRAMES
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    shutil.copy(fr_mod.templates_dir() / "level" / "5.png", bundled / "5.png")
    learned = tmp_path / "learned"
    r = FrameReader(level_templates=bundled, learned_dir=learned, clock=Clock())
    frame = load("session_06")  # Lv 44
    levels = [r.read(frame)[0] for _ in range(GUESS_CONFIRM_FRAMES + GLYPH_CONFIRM_FRAMES)]
    assert levels[:GUESS_CONFIRM_FRAMES - 1] == [None] * (GUESS_CONFIRM_FRAMES - 1)
    assert levels[-1] == 44
    assert sorted(p.name for p in learned.iterdir()) == ["4.png"]
    assert r._read_level_raw(frame, r._regions) == "44"
    assert r.hint_used is False  # a guess is not the user's settings hint
