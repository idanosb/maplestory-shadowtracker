"""Locate the status-bar value regions and the minimap title in a client-area frame.

All offsets are measured on the native 2560x1440 fixtures (see templates/README.md) and are
multiplied by the anchor's `Match.scale`. Pure image functions: no capture, no window access.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

from expstrip.paths import templates_dir
from expstrip.vision import anchors

# ---- status bar (native px, relative to the anchor's top-left) ----------------------------
EXP_OFFSET = (50, 4)       # EXP value text "114,698 [26.78%]" from the `EXP.` label
EXP_SIZE = (168, 19)
LEVEL_OFFSET = (59, -2)    # level digit tiles from the `LV.` label
LEVEL_SIZE = (52, 33)
BOTTOM_FRACTION = 0.15     # status-bar anchors are searched in the bottom 15% first
# The `EXP.` label sits this many native px right of the `LV.` label. The anchors are small, so
# their best template scale is only good to ~0.05 (4% at 1600x900, enough to misread digits);
# the distance between them gives the UI scale to ~0.2%.
LV_TO_EXP_DX = 799
SCALE_FROM_DX_MAX_DIFF = 0.08  # trust the distance only near the template's own estimate
# The client draws its UI at client height / 1440 of the native captures. A distance estimate
# within this fraction of that is snapped to it: the inventory and quickslot readers misread
# at 0.1% off (1.5006 instead of 1.5 at 3840x2160).
NATIVE_HEIGHT = 1440
SNAP_TO_HEIGHT = 0.006

# ---- minimap (native px) -------------------------------------------------------------------
WORLD_SIZE = (70, 26)            # anchor_minimap_world
# Expanded: title text (two lines beside the map icon) from the `MINI MAP` header anchor;
# the right edge is the WORLD button's right edge (the panel grows with the name).
EXPANDED_TEXT_DX = 80            # just right of the icon (icon spans header.x+4 .. +72)
EXPANDED_TEXT_DY = (32, 98)      # rows below the header anchor's top
# Collapsed: one line in a light bar ending at the -/+ buttons, which sit 51 px left of WORLD.
COLLAPSED_BUTTONS_DX = 51
COLLAPSED_TEXT_DY = (1, 27)      # rows relative to WORLD.y
COLLAPSED_TEXT_PAD = 4           # px kept clear of the bar border and the buttons
COLLAPSED_MAX_W = 900            # how far left the bar may extend from the buttons
COLLAPSED_CURSOR_GAP = 40        # dark gaps up to this wide (a mouse cursor) don't end the bar
TOP_FRACTION = 0.4               # the minimap is searched in the top-left first


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def __iter__(self):  # x, y, w, h = rect
        return iter((self.x, self.y, self.w, self.h))

    def crop(self, frame: np.ndarray) -> np.ndarray:
        return frame[max(0, self.y):max(0, self.y + self.h), max(0, self.x):max(0, self.x + self.w)]


@dataclass(frozen=True)
class Regions:
    exp: Rect
    level: Rect | None          # None when the LV label is hidden (e.g. by the Windows taskbar)
    minimap_title: Rect | None  # None when no minimap is visible
    scale: float
    minimap_form: str | None = None  # "expanded" | "collapsed" | None
    level_anchor: Rect | None = None  # where the `LV.` label matched (re-checked every read)


_TEMPLATES: dict[str, np.ndarray] = {}


def template(name: str) -> np.ndarray:
    if name not in _TEMPLATES:
        img = cv2.imread(str(templates_dir() / f"{name}.png"))
        if img is None:
            raise FileNotFoundError(name)
        _TEMPLATES[name] = img
    return _TEMPLATES[name]


def _clip(frame: np.ndarray, x: float, y: float, w: float, h: float) -> Rect | None:
    fh, fw = frame.shape[:2]
    x0, y0 = max(0, round(x)), max(0, round(y))
    x1, y1 = min(fw, round(x + w)), min(fh, round(y + h))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return Rect(x0, y0, x1 - x0, y1 - y0)


def _find_in(frame, tpl, box, scales, min_score=0.8) -> anchors.Match | None:
    """anchors.find inside box=(x0, y0, x1, y1); the match is returned in frame coordinates."""
    x0, y0, x1, y1 = box
    m = anchors.find(frame[y0:y1, x0:x1], tpl, scales=scales, min_score=min_score)
    return None if m is None else anchors.Match(m.x + x0, m.y + y0, m.scale, m.score)


def _find_bottom_first(frame, tpl, scales) -> anchors.Match | None:
    h, w = frame.shape[:2]
    m = _find_in(frame, tpl, (0, int(h * (1 - BOTTOM_FRACTION)), w, h), scales)
    return m if m is not None else anchors.find(frame, tpl, scales=scales)


def _near(scale: float) -> np.ndarray:
    return np.arange(max(0.4, scale - 0.1), scale + 0.101, 0.025)


def _refit(frame, tpl, m: anchors.Match, scale: float) -> anchors.Match:
    """`m` re-matched at exactly `scale` in a small window around it (m if that fails)."""
    th, tw = tpl.shape[:2]
    pad = round(8 + 0.2 * max(th, tw) * max(scale, m.scale))
    fh, fw = frame.shape[:2]
    box = (max(0, m.x - pad), max(0, m.y - pad),
           min(fw, m.x + round(tw * scale) + pad), min(fh, m.y + round(th * scale) + pad))
    r = _find_in(frame, tpl, box, [scale], min_score=0.6)
    return r if r is not None else m


def _rel(m: anchors.Match, frame, offset, size) -> Rect | None:
    s = m.scale
    return _clip(frame, m.x + offset[0] * s, m.y + offset[1] * s, size[0] * s, size[1] * s)


def _anchor_rect(m: anchors.Match, name: str) -> Rect:
    th, tw = template(name).shape[:2]
    return Rect(m.x, m.y, round(tw * m.scale), round(th * m.scale))


def locate_status_bar(frame: np.ndarray) -> Regions | None:
    """Regions with the EXP and level rects (no minimap), or None without an EXP label."""
    me = _find_bottom_first(frame, template("anchor_exp"), anchors.DEFAULT_SCALES)
    if me is None:
        return None
    exp = _rel(me, frame, EXP_OFFSET, EXP_SIZE)
    if exp is None:
        return None
    ml = _find_bottom_first(frame, template("anchor_lv"), _near(me.scale))
    scale = me.scale
    if ml is not None:
        s = (me.x - ml.x) / LV_TO_EXP_DX
        by_height = frame.shape[0] / NATIVE_HEIGHT
        if abs(s - by_height) <= SNAP_TO_HEIGHT * by_height:
            s = by_height  # the UI scales with the client height; exact beats a 1 px estimate
        if abs(s - me.scale) <= SCALE_FROM_DX_MAX_DIFF and abs(me.y - ml.y) < 40 * s:
            e2 = _refit(frame, template("anchor_exp"), me, s)
            l2 = _refit(frame, template("anchor_lv"), ml, s)
            if abs(e2.scale - s) < 1e-3 and abs(l2.scale - s) < 1e-3:  # else keep the estimates
                me, ml, scale = e2, l2, s  # anchors.find rounds scales to 4 decimals
                exp = _rel(me, frame, EXP_OFFSET, EXP_SIZE)
                if exp is None:
                    return None
    else:
        # LV hidden (the taskbar over a windowed client): no distance, but the template's
        # estimate is within half a step of client height / 1440 when the UI follows it.
        by_height = frame.shape[0] / NATIVE_HEIGHT
        if abs(me.scale - by_height) <= 0.03:
            e2 = _refit(frame, template("anchor_exp"), me, by_height)
            if abs(e2.scale - by_height) < 1e-3:
                me, scale = e2, by_height
                exp = _rel(me, frame, EXP_OFFSET, EXP_SIZE)
                if exp is None:
                    return None
    level = _rel(ml, frame, LEVEL_OFFSET, LEVEL_SIZE) if ml is not None else None
    return Regions(exp, level, None, scale,
                   level_anchor=_anchor_rect(ml, "anchor_lv") if level is not None else None)


def _collapsed_left(frame: np.ndarray, world: anchors.Match, right: int) -> int:
    """Left edge of the collapsed title bar: scan left from the buttons along the bar's light
    background (rows just under the bar's top border) until it ends."""
    s = world.scale
    r0 = max(0, round(world.y + 1 * s))
    r1 = max(r0 + 1, round(world.y + 4 * s))
    lo = max(0, round(right - COLLAPSED_MAX_W * s))
    band = frame[r0:r1, lo:right]
    if band.size == 0:
        return lo
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    light = ((hsv[..., 2] >= 180) & (hsv[..., 1] < 40)).all(axis=0)
    # Walk left over light columns; a short dark gap (the mouse cursor over the bar) is
    # tolerated, a long one is the end of the bar.
    max_gap = max(2, round(COLLAPSED_CURSOR_GAP * s))
    left, x = light.size, light.size - 1
    while x >= 0:
        if light[x]:
            left = x
            x -= 1
            continue
        g = x
        while g >= 0 and not light[g] and x - g < max_gap:
            g -= 1
        if g < 0 or not light[g]:
            break  # the gap reached the frame edge or is too long
        x = g
    return lo + left


def locate_minimap(frame: np.ndarray, scale: float | None = None) -> tuple[Rect | None, str | None]:
    """(title rect, form) of the minimap, or (None, None)."""
    h, w = frame.shape[:2]
    scales = _near(scale) if scale is not None else anchors.DEFAULT_SCALES
    tw = template("anchor_minimap_world")
    world = _find_in(frame, tw, (0, 0, w // 2, int(h * TOP_FRACTION)), scales)
    if world is None:
        world = anchors.find(frame, tw, scales=scales)
    if world is None:
        return None, None
    s = world.scale
    # The expanded header text sits on the same rows as the buttons, to their left.
    y0 = max(0, round(world.y - 20 * s))
    y1 = min(h, round(world.y + 40 * s))
    x0 = max(0, round(world.x - 1200 * s))
    head = _find_in(frame, template("anchor_minimap_expanded"), (x0, y0, world.x, y1), _near(s))
    world_right = world.x + WORLD_SIZE[0] * s
    if head is not None:
        left = head.x + EXPANDED_TEXT_DX * s
        top = head.y + EXPANDED_TEXT_DY[0] * s
        rect = _clip(frame, left, top, world_right - left, (EXPANDED_TEXT_DY[1] - EXPANDED_TEXT_DY[0]) * s)
        return rect, "expanded"
    # "WORLD found and not expanded" = collapsed (templates/README.md).
    right = round(world.x - (COLLAPSED_BUTTONS_DX + COLLAPSED_TEXT_PAD) * s)
    left = _collapsed_left(frame, world, right) + COLLAPSED_TEXT_PAD * s
    top = world.y + COLLAPSED_TEXT_DY[0] * s
    rect = _clip(frame, left, top, right - left, (COLLAPSED_TEXT_DY[1] - COLLAPSED_TEXT_DY[0]) * s)
    return rect, "collapsed"


def locate(frame: np.ndarray) -> Regions | None:
    """Regions of the EXP text, level tiles and minimap title, or None without an EXP label."""
    if frame is None or frame.ndim != 3 or frame.size == 0:
        return None
    if frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    bar = locate_status_bar(frame)
    if bar is None:
        return None
    title, form = locate_minimap(frame, bar.scale)
    return replace(bar, minimap_title=title, minimap_form=form)
