"""Read the exact meso total from the ITEM INVENTORY window, when the player has it open.

The window can sit anywhere on the client. At its bottom is a white field with the total
("1,045,605") and a "MESOS" label to its right; the field is shown on every inventory tab.

1. The anchor (templates/anchor_mesos.png: the field's right border and the MESOS label) is
   found over the whole frame at `scale` only.
2. The field's interior is cut at a fixed offset from the anchor and must be white all along
   its edge ring (an item tooltip or another window over the field is never read).
3. The text is read with glyphs mode "mesos"; every character must read confidently and the
   digits must be grouped by commas every three, otherwise the result is None.
Pure image functions: no capture, no window access. Offsets are native px at scale 1.0.
"""
from __future__ import annotations

import re

import cv2
import numpy as np

from expstrip.paths import templates_dir
from expstrip.vision import anchors
from expstrip.vision.glyphs import GlyphSet, read
from expstrip.vision.layout import Rect, template

# The anchor template's top-left to the field interior (the white area inside its border).
FIELD_DX, FIELD_DY = -267, 5
FIELD_W, FIELD_H = 264, 23
ANCHOR_MIN_SCORE = 0.8
_WHITE = 235          # field background brightness (255); digits and borders are far darker
_RING = 2             # px ring along the crop edge that must be white
_MIN_WHITE_RING = 0.98
_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+$")
_COARSE = 4           # the anchor is first searched on a frame shrunk by this factor

_DIGITS: GlyphSet | None = None


def _glyphs() -> GlyphSet:
    global _DIGITS
    if _DIGITS is None:
        _DIGITS = GlyphSet.load(templates_dir() / "mesos")
    return _DIGITS


def field_rect(anchor_x: int, anchor_y: int, scale: float) -> Rect:
    return Rect(anchor_x + round(FIELD_DX * scale), anchor_y + round(FIELD_DY * scale),
                round(FIELD_W * scale), round(FIELD_H * scale))


def find_anchor(frame: np.ndarray, scale: float) -> anchors.Match | None:
    """The MESOS anchor at `scale`: a coarse pass on a shrunk grayscale frame, then a refine
    at full resolution in a small window around the best coarse hit."""
    tpl = template("anchor_mesos")
    g = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    d = _COARSE
    small = cv2.resize(g, (g.shape[1] // d, g.shape[0] // d), interpolation=cv2.INTER_AREA)
    tg = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY) if tpl.ndim == 3 else tpl
    ts = cv2.resize(tg, (max(1, round(tg.shape[1] * scale / d)), max(1, round(tg.shape[0] * scale / d))),
                    interpolation=cv2.INTER_AREA)
    if ts.shape[0] > small.shape[0] or ts.shape[1] > small.shape[1]:
        return None
    res = cv2.matchTemplate(small, ts, cv2.TM_CCOEFF_NORMED)
    np.nan_to_num(res, copy=False, nan=-1.0, posinf=-1.0, neginf=-1.0)
    _, _, _, (cx, cy) = cv2.minMaxLoc(res)
    cx, cy = cx * d, cy * d
    th, tw = round(tg.shape[0] * scale), round(tg.shape[1] * scale)
    pad = 2 * d + 2
    x0, y0 = max(0, cx - pad), max(0, cy - pad)
    win = g[y0:cy + th + pad, x0:cx + tw + pad]
    m = anchors.find(win, tpl, [scale], ANCHOR_MIN_SCORE)
    if m is None:
        return None
    return anchors.Match(m.x + x0, m.y + y0, m.scale, m.score)


def field_is_white(crop: np.ndarray) -> bool:
    """True when the edge ring of the field crop is the field's white (nothing covers it)."""
    if crop.ndim == 3:
        crop = crop.max(axis=2)
    r = max(1, min(_RING, crop.shape[0] // 4, crop.shape[1] // 4))
    ring = np.concatenate([crop[:r].ravel(), crop[-r:].ravel(), crop[:, :r].ravel(), crop[:, -r:].ravel()])
    return float((ring >= _WHITE).mean()) >= _MIN_WHITE_RING


def read_inventory_mesos(frame: np.ndarray, scale: float) -> int | None:
    """The meso total shown in the inventory window, or None (window not visible, field covered,
    or any character unreadable / the digit grouping invalid)."""
    if frame is None or frame.size == 0:
        return None
    m = find_anchor(frame, scale)
    if m is None:
        return None
    r = field_rect(m.x, m.y, m.scale)
    fh, fw = frame.shape[:2]
    if r.x < 0 or r.y < 0 or r.x + r.w > fw or r.y + r.h > fh:
        return None
    crop = r.crop(frame)
    if not field_is_white(crop):
        return None
    text = read(crop, _glyphs(), m.scale, "mesos")
    if not _GROUPED.match(text):
        return None
    return int(text.replace(",", ""))


class MesosConfirmer:
    """Passes a total on only when two consecutive reads agree; a None read resets."""

    def __init__(self) -> None:
        self._prev: int | None = None

    def reset(self) -> None:
        self._prev = None

    def feed(self, v: int | None) -> int | None:
        out = v if v is not None and v == self._prev else None
        self._prev = v
        return out
