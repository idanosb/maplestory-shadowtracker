"""One captured client-area frame -> (level, (exp, pct), MapName).

Called about once a second from the engine's worker thread. Never raises out of `read`.
Pure image work plus Windows OCR: no window, process or capture access here.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from expstrip.paths import templates_dir
from expstrip.reading import MapName, normalize_map_name, parse_exp_text
from expstrip.vision import glyphs, layout
from expstrip.vision.layout import Rect, Regions

log = logging.getLogger(__name__)

OCR_INTERVAL = 3.0          # s between map-title OCR runs; the last result is reused in between
MINIMAP_INTERVAL = 30.0     # s between minimap re-locates (the window can be dragged)
RELOCATE_AFTER_FAILS = 5    # consecutive failed EXP parses before a full re-locate
DEBUG_INTERVAL = 1.0        # s between debug dumps
DEBUG_UNLOCATED_INTERVAL = 10.0  # s between full-frame dumps while no EXP bar is found
LEVEL_UP_PCT_DROP = 50.0    # pct drop (points) that marks a level-up when the digits are unreadable
LEVEL_UP_CONFIRM_FRAMES = 2 # consecutive frames that must show the drop before a level-up is inferred
GLYPH_CONFIRM_FRAMES = 3    # consecutive frames with agreeing tile glyphs before a glyph is learned
GLYPH_AGREE_SCORE = 0.9     # glyphs.score between a frame's glyph and the first capture
KNOWN_AFTER_READS = 2       # OCR reads of the same complete name before it becomes "known"
SNAP_RATIO = 0.85           # difflib ratio for snapping an OCR name onto a known one
ANCHOR_RECHECK_SCORE = 0.8  # the cached LV label must still match this well, else level=None

Result = tuple[int | None, tuple[int, float] | None, MapName | None]


# ------------------------------------------------------------------------- pure helpers

def is_level_up_drop(ep: tuple[int, float] | None, last_ep: tuple[int, float] | None) -> bool:
    """One frame that looks like a level-up: both the absolute EXP and the pct fell, the pct by
    more than LEVEL_UP_PCT_DROP points. A pct misread alone (e.g. "99.12" -> "9.12") leaves the
    absolute EXP up, so it does not count."""
    if ep is None or last_ep is None:
        return False
    return ep[0] < last_ep[0] and last_ep[1] - ep[1] > LEVEL_UP_PCT_DROP


def infer_level(raw: str | None, last_level: int | None, drop_frames: int,
                level_hint: int | None) -> tuple[int | None, bool]:
    """(level, leveled_up) from the raw tile text (ruling A, fix round 1). `drop_frames` is the
    number of consecutive frames, this one included, for which is_level_up_drop held. Only call
    it when the LV label is visible; with the label hidden the level is simply None."""
    if raw and raw.isdigit():
        return int(raw), False
    if last_level is not None:
        if drop_frames >= LEVEL_UP_CONFIRM_FRAMES:
            return last_level + 1, True
        return last_level, False
    return level_hint, False


def hint_fits(raw: str | None, level: int) -> bool:
    """May the tile glyphs be learned as `level` (a user hint)? Only when the tile has as many
    glyphs as the level has digits and at least one is read, matching the hint at its place
    ("4?" fits 46); a stale hint whose read digits disagree ("5?" for 46) teaches nothing."""
    digits = str(level)
    if not raw or len(raw) != len(digits):
        return False
    known = [(a, b) for a, b in zip(raw, digits) if a.isdigit()]
    return bool(known) and all(a == b for a, b in known)


# A level digit with no tile template yet is guessed from the EXP digits (same pixel font, a
# little smaller), resized to the tile glyph: on the four learned tile digits the right digit
# scored 0.71-0.79 and led the next by >= 0.08. The guess only seeds the level; the real tile
# glyphs are then learned from it (as from a user level_hint).
GUESS_MIN_SCORE = 0.62
GUESS_MIN_GAP = 0.05
GUESS_CONFIRM_FRAMES = 3   # the same guessed level on this many consecutive frames


def guess_level_digit(glyph: np.ndarray, exp_gs: glyphs.GlyphSet) -> str | None:
    """The digit whose EXP-font template, resized to `glyph`, matches it best; None if unsure."""
    h, w = glyph.shape[:2]
    scores = []
    for d in "0123456789":
        imgs = exp_gs.templates.get(d, [])
        if imgs:
            scores.append((max(glyphs.score(glyph, cv2.resize(t, (w, h), interpolation=cv2.INTER_LINEAR))
                               for t in imgs), d))
    scores.sort(reverse=True)
    if len(scores) < 2 or scores[0][0] < GUESS_MIN_SCORE or scores[0][0] - scores[1][0] < GUESS_MIN_GAP:
        return None
    return scores[0][1]


_DIGIT_LOOKALIKES = str.maketrans({"l": "1", "I": "1", "|": "1", "!": "1", "O": "0", "o": "0"})


def _digits(s: str) -> list[str]:
    """Numbers in the numeric tokens of `s`: whitespace-separated tokens that hold at least one
    real digit ("1", "1>", "1O"). Lookalikes are mapped to digits only inside those tokens, so
    letters in words ("Ellinia", "Road") never count as digits."""
    out: list[str] = []
    for tok in s.split():
        if re.search(r"\d", tok):
            out.extend(re.findall(r"\d+", tok.translate(_DIGIT_LOOKALIKES)))
    return out


def snap_map_name(m: MapName, known: dict[str, MapName]) -> MapName:
    """The known name closest to `m` when its ratio is >= SNAP_RATIO, else `m`.

    Names whose numbers differ ("<Area 1>" vs "<Area 2>") never snap onto each other: they are
    different maps that OCR tells apart reliably, and their ratio is far above SNAP_RATIO.
    """
    if m.full in known:
        return known[m.full]
    best, best_ratio = None, 0.0
    for full, km in known.items():
        if _digits(full) != _digits(m.full):
            continue
        r = difflib.SequenceMatcher(None, m.full, full).ratio()
        if r > best_ratio:
            best, best_ratio = km, r
    return best if best is not None and best_ratio >= SNAP_RATIO else m


def _complete(m: MapName) -> bool:
    """Plausibly whole: a region and a map, balanced brackets (a dropped "1>" leaves "<Area")."""
    f = m.full
    return bool(m.region and m.map) and f.count("<") == f.count(">") and f.count("(") == f.count(")")


# ------------------------------------------------------------------------- title OCR prep

def _pad(img: np.ndarray, px: int, value: int = 255) -> np.ndarray:
    return cv2.copyMakeBorder(img, px, px, px, px, cv2.BORDER_CONSTANT, value=value)


def _expanded_variants(native: np.ndarray) -> list[np.ndarray]:
    """Light bold text with a shadow on blue-grey. Binarize the light core, thin it, black on
    white. Chosen on the fixtures: without the erode Windows OCR drops the "1>" of
    "<Area 1>"; ocr_lines doubles the image again."""
    hsv = cv2.cvtColor(native, cv2.COLOR_BGR2HSV)
    light = ((hsv[..., 2] > 170) & (hsv[..., 1] < 60)).astype(np.uint8) * 255
    big = cv2.resize(light, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, big = cv2.threshold(big, 127, 255, cv2.THRESH_BINARY)
    out = []
    for k in (2, 3):
        img = _pad(255 - cv2.erode(big, np.ones((k, k), np.uint8)), 20)
        img = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        out.append(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    out.append(native)
    return out


def _collapsed_variants(native: np.ndarray) -> list[np.ndarray]:
    """Dark thin text on a light bar: binarize at 150, upscale 2x (ocr_lines doubles again)."""
    gray = cv2.cvtColor(native, cv2.COLOR_BGR2GRAY)
    _, b = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    b = cv2.resize(b, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    up = cv2.resize(native, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return [cv2.cvtColor(b, cv2.COLOR_GRAY2BGR), up, native]


def title_variants(crop: np.ndarray, form: str | None, scale: float) -> list[np.ndarray]:
    native = glyphs.to_native(crop, scale)
    if form == "expanded":
        return _expanded_variants(native)
    return _collapsed_variants(native)


def _default_ocr(img: np.ndarray) -> list[str]:
    from expstrip.vision.ocr import ocr_lines  # winsdk import deferred to first use
    return ocr_lines(img)


# ------------------------------------------------------------------------- the reader

class FrameReader:
    def __init__(
        self,
        debug_dir: Path | None = None,
        learned_dir: Path | None = None,
        level_hint: int | None = None,
        known_maps: Iterable[str] = (),
        level_templates: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._debug_dir = Path(debug_dir) if debug_dir is not None else None
        self._learned_dir = Path(learned_dir) if learned_dir is not None else None
        self._level_hint = level_hint
        self.hint_used = False                     # True once level_hint gave the level
        self._auto_hint = False                    # _level_hint came from guess_level_digit
        self._guess: tuple[int, int] | None = None  # (guessed level, consecutive frames)
        self._clock = clock
        self._exp_gs = glyphs.GlyphSet.load(templates_dir() / "exp")
        bundled = Path(level_templates) if level_templates is not None else templates_dir() / "level"
        self._level_gs = glyphs.GlyphSet.load_many([bundled, self._learned_dir])
        self._known: dict[str, MapName] = {}
        for s in known_maps:
            m = normalize_map_name([s])
            if m is not None:
                self._known[m.full] = m
        self._ocr: Callable[[np.ndarray], list[str]] = _default_ocr

        self._regions: Regions | None = None
        self._size: tuple[int, int] | None = None
        self._located = False
        self._fails = 0
        self._minimap_t = 0.0
        self._ocr_t: float | None = None
        self._map: MapName | None = None
        self._seen: dict[str, int] = {}           # complete OCR name -> times read
        self._last_level: int | None = None
        self._last_ep: tuple[int, float] | None = None  # last valid (exp, pct) at a known level
        self._drop_frames = 0                      # consecutive level-up-looking frames
        self._learn_level: int | None = None       # inferred level whose glyphs may be learned
        self._learn_cand: list[np.ndarray] | None = None
        self._learn_n = 0
        self._warned: set[tuple[str, str]] = set()
        self._debug_t: float | None = None
        self._debug_full_t: float | None = None
        self._lv_tpl: tuple[float, np.ndarray] | None = None

    # -- public ---------------------------------------------------------------------------

    @property
    def scale(self) -> float | None:
        """The UI scale of the located regions, or None until the EXP bar has been found."""
        return self._regions.scale if self._regions is not None else None

    @property
    def regions(self) -> Regions | None:
        """The located status-bar / minimap regions, or None until the EXP bar has been found."""
        return self._regions

    def read(self, frame: np.ndarray) -> Result:
        level = ep = m = None
        try:
            if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.size == 0:
                return None, None, None
            if frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            now = self._clock()
            self._ensure_regions(frame, now)
            if self._regions is None:
                self._fails += 1
                self._guard("debug", self._debug_unlocated, frame, now)
                return None, None, None

            ep = self._guard("exp", self._read_exp, frame, self._regions)
            if ep is None:
                self._fails += 1
                if self._fails >= RELOCATE_AFTER_FAILS:
                    self._relocate(frame, now)
                    if self._regions is None:
                        return None, None, None
                    ep = self._guard("exp", self._read_exp, frame, self._regions)
            if ep is not None:
                self._fails = 0

            level = self._guard("level", self._level, frame, self._regions, ep)
            if ep is not None and level is not None and self._drop_frames == 0:
                # Only while the level is known (a level-up that happens while the taskbar hides
                # the tiles is still seen as a drop once they are visible again), and not while a
                # drop awaits confirmation (the next frame is compared with the value before it).
                self._last_ep = ep
            if self._drop_frames > 0:
                # A level-up drop awaits confirmation: the level is still the old one, so the
                # post-drop EXP must not be reported with it (it would read as a huge loss).
                ep = None
            m = self._guard("map", self._map_name, frame, now)
            self._guard("debug", self._debug, frame, now, level, ep, m)
        except Exception as e:  # noqa: BLE001 - read() must never raise into the engine
            self._log_exc("read", e)
        return level, ep, m

    # -- regions --------------------------------------------------------------------------

    def _relocate(self, frame: np.ndarray, now: float) -> None:
        self._regions = layout.locate(frame)
        self._size = frame.shape[:2]
        self._located = True
        self._fails = 0
        self._minimap_t = now
        self._invalidate_map()

    def _ensure_regions(self, frame: np.ndarray, now: float) -> None:
        if not self._located or frame.shape[:2] != self._size:
            self._relocate(frame, now)
        elif self._regions is None:
            if self._fails >= RELOCATE_AFTER_FAILS:
                self._relocate(frame, now)
        elif now - self._minimap_t >= MINIMAP_INTERVAL:
            self._minimap_t = now
            if self._regions.level_anchor is None:
                # Started while the taskbar hid the LV label: EXP keeps parsing, so no full
                # re-locate would ever happen. Look for the level again on the same cadence.
                bar = layout.locate_status_bar(frame)
                if bar is not None and bar.level_anchor is not None and bar.scale == self._regions.scale:
                    self._regions = replace(self._regions, level=bar.level,
                                            level_anchor=bar.level_anchor)
            title, form = layout.locate_minimap(frame, self._regions.scale)
            if (title, form) != (self._regions.minimap_title, self._regions.minimap_form):
                self._regions = replace(self._regions, minimap_title=title, minimap_form=form)
                self._invalidate_map()

    def _invalidate_map(self) -> None:
        self._ocr_t = None
        self._map = None

    # -- exp ------------------------------------------------------------------------------

    def _read_exp(self, frame: np.ndarray, regions: Regions) -> tuple[int, float] | None:
        text = glyphs.read(regions.exp.crop(frame), self._exp_gs, regions.scale, "exp")
        return parse_exp_text(text)

    # -- level ----------------------------------------------------------------------------

    def _lv_label_visible(self, frame: np.ndarray, regions: Regions) -> bool:
        """Cheap re-check that the LV label is still where it was located (the taskbar can
        slide over it while the regions are cached)."""
        a = regions.level_anchor
        if a is None:
            return False
        if self._lv_tpl is None or self._lv_tpl[0] != regions.scale:
            tpl = cv2.cvtColor(layout.template("anchor_lv"), cv2.COLOR_BGR2GRAY)
            self._lv_tpl = (regions.scale, cv2.resize(tpl, (a.w, a.h), interpolation=cv2.INTER_AREA))
        tpl = self._lv_tpl[1]
        pad = 3
        win = Rect(a.x - pad, a.y - pad, a.w + 2 * pad, a.h + 2 * pad).crop(frame)
        if win.shape[0] < tpl.shape[0] or win.shape[1] < tpl.shape[1]:
            return False
        res = cv2.matchTemplate(cv2.cvtColor(win, cv2.COLOR_BGR2GRAY), tpl, cv2.TM_CCOEFF_NORMED)
        np.nan_to_num(res, copy=False, nan=-1.0, posinf=-1.0, neginf=-1.0)
        return float(res.max()) >= ANCHOR_RECHECK_SCORE

    def _read_level_raw(self, frame: np.ndarray, regions: Regions) -> str | None:
        """The tile text ("44", "4?", ""), or None when the LV label is not visible."""
        if regions.level is None or not self._lv_label_visible(frame, regions):
            return None
        return glyphs.read(regions.level.crop(frame), self._level_gs, regions.scale, "level")

    def _level(self, frame: np.ndarray, regions: Regions, ep: tuple[int, float] | None) -> int | None:
        raw = self._read_level_raw(frame, regions)
        if raw is None:
            self._learn_cand, self._learn_n = None, 0  # glyph frames must be consecutive
            return None  # LV label hidden (taskbar): no inference (ruling A.4)
        readable = bool(raw) and raw.isdigit()
        if not readable and self._last_level is not None and is_level_up_drop(ep, self._last_ep):
            self._drop_frames += 1
        else:
            self._drop_frames = 0
        if not readable and self._last_level is None and self._level_hint is None and raw:
            self._guard("guess", self._guess_level, frame, regions, raw)
        level, leveled_up = infer_level(raw, self._last_level, self._drop_frames, self._level_hint)
        if self._last_level is None and level is not None and level == self._level_hint and not readable:
            self._level_hint = None  # the hint is used once
            auto, self._auto_hint = self._auto_hint, False
            if not auto:
                self.hint_used = True
            if hint_fits(raw, level) or (auto and len(raw) == len(str(level))):
                # learn the unknown digits from the hinted (or guessed) level
                self._learn_level, self._learn_cand, self._learn_n = level, None, 0
        if leveled_up:
            self._drop_frames = 0
            self._learn_level, self._learn_cand, self._learn_n = level, None, 0
        if readable or level != self._learn_level:
            self._learn_level, self._learn_cand, self._learn_n = None, None, 0
        if self._learn_level is not None:
            self._guard("learn", self._learn_level_glyphs, frame, regions, level)
        if level is not None:
            self._last_level = level
        return level

    def _guess_level(self, frame: np.ndarray, regions: Regions, raw: str) -> None:
        """No tile template for some digit and no hint: guess the level from the EXP font. A
        guess that holds for GUESS_CONFIRM_FRAMES frames becomes the level hint."""
        native = glyphs.to_native(regions.level.crop(frame), regions.scale)
        imgs = glyphs.glyph_images(native, "level")
        guess = None
        if len(imgs) == len(raw) and 1 <= len(imgs) <= 3:
            digits = [ch if ch.isdigit() else guess_level_digit(img, self._exp_gs)
                      for img, ch in zip(imgs, raw)]
            if all(digits) and digits[0] != "0":
                guess = int("".join(digits))
        if guess is None:
            self._guess = None
            return
        n = self._guess[1] + 1 if self._guess is not None and self._guess[0] == guess else 1
        self._guess = (guess, n)
        if n >= GUESS_CONFIRM_FRAMES:
            self._guess = None
            self._level_hint, self._auto_hint = guess, True
            log.info("level %d guessed from the EXP font (tile read %r)", guess, raw)

    def _learn_level_glyphs(self, frame: np.ndarray, regions: Regions, level: int) -> None:
        """Pair the tile glyphs with the digits of an inferred `level`. Only after
        GLYPH_CONFIRM_FRAMES consecutive frames give agreeing glyphs (same count, each scoring
        >= GLYPH_AGREE_SCORE against the first capture) is each digit that had no template added
        to the level GlyphSet and saved to learned_dir. Until then it is not used at all."""
        native = glyphs.to_native(regions.level.crop(frame), regions.scale)
        imgs = glyphs.glyph_images(native, "level")
        digits = str(level)
        if len(imgs) != len(digits):
            self._learn_cand, self._learn_n = None, 0
            return
        agree = self._learn_cand is not None and all(
            glyphs.score(a, b) >= GLYPH_AGREE_SCORE for a, b in zip(imgs, self._learn_cand))
        if agree:
            self._learn_n += 1
        else:
            self._learn_cand, self._learn_n = imgs, 1
        if self._learn_n < GLYPH_CONFIRM_FRAMES:
            return
        cand = self._learn_cand
        self._learn_level, self._learn_cand, self._learn_n = None, None, 0
        for img, d in zip(cand, digits):
            if d in self._level_gs.templates:
                continue
            self._level_gs.templates[d] = [img.astype(np.float32)]
            if self._learned_dir is not None:
                self._learned_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(self._learned_dir / f"{d}.png"),
                            np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8))
            log.info("learned level glyph %r", d)

    # -- map ------------------------------------------------------------------------------

    def _map_name(self, frame: np.ndarray, now: float) -> MapName | None:
        r = self._regions
        if r is None or r.minimap_title is None:
            return None
        if self._ocr_t is not None and now - self._ocr_t < OCR_INTERVAL:
            return self._map
        self._ocr_t = now
        self._map = self._ocr_title(r.minimap_title.crop(frame), r.minimap_form, r.scale)
        return self._map

    def _ocr_title(self, crop: np.ndarray, form: str | None, scale: float) -> MapName | None:
        """Try the preprocessing variants in order; take the first plausibly complete name,
        snapped onto a known one."""
        first = None
        for img in title_variants(crop, form, scale):
            m = normalize_map_name(self._ocr(img))
            if m is None:
                continue
            if form == "expanded" and not m.region:
                continue  # one line only: the other line was lost
            if _complete(m):
                snapped = snap_map_name(m, self._known)
                if snapped.full not in self._known:
                    # A name becomes a snap target only once it was read KNOWN_AFTER_READS
                    # times, so one plausible misread can't attract later reads.
                    self._seen[snapped.full] = self._seen.get(snapped.full, 0) + 1
                    if self._seen[snapped.full] >= KNOWN_AFTER_READS:
                        self._known[snapped.full] = snapped
                return snapped
            first = first or m
        return snap_map_name(first, self._known) if first is not None else None

    # -- debug / errors -------------------------------------------------------------------

    def _guard(self, what: str, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001
            self._log_exc(what, e)
            return None

    def _log_exc(self, what: str, e: BaseException) -> None:
        key = (type(e).__name__, str(e))
        if key not in self._warned:  # once per distinct error, so a permanent fault is visible
            self._warned.add(key)
            log.warning("frame reader: %s failed: %s: %s", what, key[0], key[1], exc_info=e)
        else:
            log.debug("frame reader: %s failed", what, exc_info=e)
        if self._debug_dir is None:
            return
        try:
            self._append_log(f"ERROR in {what}: {e!r}\n" + "".join(traceback.format_exception(e)))
        except Exception:  # noqa: BLE001
            pass

    def _append_log(self, line: str) -> None:
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        with open(self._debug_dir / "readings.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='milliseconds')} {line.rstrip()}\n")

    def _debug_unlocated(self, frame: np.ndarray, now: float) -> None:
        """No EXP bar found: save the whole client frame (rarely), so the cause can be seen."""
        if self._debug_dir is None:
            return
        if self._debug_full_t is not None and now - self._debug_full_t < DEBUG_UNLOCATED_INTERVAL:
            return
        self._debug_full_t = now
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        cv2.imwrite(str(self._debug_dir / f"{stamp}_full_unlocated.png"), frame)
        self._append_log(f"{stamp} EXP bar not found; frame {frame.shape[1]}x{frame.shape[0]}")

    def _debug(self, frame: np.ndarray, now: float, level, ep, m) -> None:
        if self._debug_dir is None:
            return
        if self._debug_t is not None and now - self._debug_t < DEBUG_INTERVAL:
            return
        self._debug_t = now
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        r = self._regions
        for name, rect in (("exp", r.exp), ("level", r.level), ("map", r.minimap_title)):
            if rect is not None:
                cv2.imwrite(str(self._debug_dir / f"{stamp}_{name}.png"), rect.crop(frame))
        self._append_log(f"{stamp} level={level} exp_pct={ep} map={m.full if m else None} "
                         f"scale={r.scale:.3f} form={r.minimap_form} "
                         f"frame={frame.shape[1]}x{frame.shape[0]}")
        if ep is None and (self._debug_full_t is None
                           or now - self._debug_full_t >= DEBUG_UNLOCATED_INTERVAL):
            self._debug_full_t = now  # EXP misread: the whole frame shows what the crop missed
            cv2.imwrite(str(self._debug_dir / f"{stamp}_full.png"), frame)
