"""Read how many of each potion the player has from the numbers on the quickslot keys.

The quickslot grid (4x3 keys) sits at the bottom right of the client, left of the Cash Shop /
Menu / Shortcut buttons. A key holding an item shows its icon, and at the key's bottom left
the total count of that item across all inventory stacks: white digits with a dark outline
(glyphs mode "count"). Offsets are native px at scale 1.0 (see templates/README.md).

1. Each requested potion's icon (templates/potions/<Name>.png, a strip of the icon between
   the key label and the count, so any key label is left out) is found with anchors.find
   near `scale`, inside the bottom-right quarter of the frame only.
2. The count is read from a fixed box at the icon's bottom left.
A potion on two keys shows the same total on both, so the best match is enough.

For learning a new potion's icon (expstrip.icon_learner) the 12 keys can also be read
without knowing their icons: key_origins() places the grid from the client's bottom-right
corner (snapped to a known icon when one is on a key), read_key_counts() reads every key's
count and key_icon() cuts the icon template of one key. Learned icons live in extra folders
(add_icon_dir). Pure image functions: no capture, no window access.
"""
from __future__ import annotations

import cv2
import numpy as np

from expstrip.paths import templates_dir
from expstrip.vision import anchors
from expstrip.vision.glyphs import GlyphSet, read
from expstrip.vision.layout import Rect

# ---- geometry (native px at scale 1.0) ------------------------------------------------------
# The icon template is the key's rows 22..35 and columns 2..57 (key = the light square,
# 60x57 px on the 2560x1440 client); the count box starts 6 px left of the template (on the
# key's dark border) and holds the digit band (key rows 37..55) with glyphs.COUNT_PAD rows of
# slack above and below.
COUNT_DX, COUNT_DY = -6, 12   # count box from the icon template's top-left
COUNT_W, COUNT_H = 66, 25
ICON_MIN_SCORE = 0.85         # the icon matches itself at ~1.0; other icons / UI reach <= 0.75
COLOR_MAX_DIFF = 8.0          # colour check of a grey match: vials of one shape and another colour
                              # (Magic blue / Sniper pink / Speed red) match in grey at up to 0.98,
                              # so the patch's mean B, G and R must each be within this of the
                              # icon's. The right icon is within 2.5 (also at scale 0.75, where
                              # per-pixel differences reach ~9 from resampling); red Speed vs pink
                              # Sniper differ by ~18 on one channel, Magic vs Sniper by ~26.
_COLOR_TRIES = 4              # grey candidates tried before giving up on a colour mismatch
SCALE_TOLERANCE = (0.97, 1.0, 1.03)  # icons are searched only near the client's UI scale
_REFINE_PAD = 6               # px around the coarse hit in which the nearby scales are scored
# Key grid (2560x1440 client, scale 1.0): 4 columns x 3 rows of 60x57 keys at a ~66.3 px
# pitch; the middle row (keys 1-4) starts at x 1761, y 1181, i.e. 799 / 259 px from the
# client's right / bottom edge. The icon template sits 2 px right and 22 px below a key's
# top-left.
KEY_PITCH = 66.3
GRID_RIGHT, GRID_BOTTOM = 799, 259   # key "1" top-left, from the client's right / bottom edge
ICON_DX, ICON_DY = 2, 22
ICON_W, ICON_H = 56, 14
_SNAP = 20                    # px a known icon may move the computed grid
_EXTRA_ICON_DIRS: list = []

_ICONS: dict[str, np.ndarray] | None = None
_ICONS_BGR: dict[str, np.ndarray] = {}
_DIGITS: GlyphSet | None = None


def _icons() -> dict[str, np.ndarray]:
    """{name: grey icon}; the colour versions go to _ICONS_BGR for the colour check."""
    global _ICONS
    if _ICONS is None:
        out = {}
        _ICONS_BGR.clear()
        for folder in [templates_dir() / "potions", *_EXTRA_ICON_DIRS]:
            for p in sorted(folder.glob("*.png")) if folder.is_dir() else []:
                img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                if img is not None and p.stem not in out:
                    out[p.stem] = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    _ICONS_BGR[p.stem] = img
        _ICONS = out
    return _ICONS


def _colour_ok(frame: np.ndarray, name: str, m: anchors.Match) -> bool:
    """Does the frame at match m have the icon's colours (not just its grey shape)?"""
    tpl = _ICONS_BGR.get(name)
    if tpl is None or frame.ndim != 3:
        return True
    th, tw = tpl.shape[:2]
    w, h = max(2, round(tw * m.scale)), max(2, round(th * m.scale))
    patch = frame[m.y:m.y + h, m.x:m.x + w, :3]
    if patch.shape[0] != h or patch.shape[1] != w:
        return False
    t = cv2.resize(tpl, (w, h), interpolation=cv2.INTER_AREA) if (w, h) != (tw, th) else tpl
    diff = np.abs(patch.reshape(-1, 3).mean(axis=0) - t.reshape(-1, 3).mean(axis=0))
    return float(diff.max()) <= COLOR_MAX_DIFF


def add_icon_dir(folder) -> None:
    """Also load potion icons from `folder` (learned at runtime)."""
    from pathlib import Path
    folder = Path(folder)
    if folder not in _EXTRA_ICON_DIRS:
        _EXTRA_ICON_DIRS.append(folder)
        reload_icons()


def reload_icons() -> None:
    global _ICONS
    _ICONS = None


def has_icon(name: str) -> bool:
    return name in _icons()


def _digits() -> GlyphSet:
    global _DIGITS
    if _DIGITS is None:
        _DIGITS = GlyphSet.load(templates_dir() / "count")
    return _DIGITS


def _quarter(frame: np.ndarray) -> tuple[np.ndarray, int, int]:
    fh, fw = frame.shape[:2]
    x0, y0 = fw // 2, fh // 2
    q = frame[y0:, x0:]
    if q.ndim == 3:
        q = cv2.cvtColor(q, cv2.COLOR_BGRA2GRAY if q.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    return q, x0, y0


def count_rect(icon_x: int, icon_y: int, scale: float) -> Rect:
    """The count box of a key whose icon template was found at (icon_x, icon_y), frame px."""
    return Rect(icon_x + round(COUNT_DX * scale), icon_y + round(COUNT_DY * scale),
                round(COUNT_W * scale), round(COUNT_H * scale))


def find_icon(frame: np.ndarray, scale: float, name: str, _quarter_gray=None) -> anchors.Match | None:
    """Best match of potion `name`'s icon in the bottom-right quarter (frame coordinates)."""
    tpl = _icons().get(name)
    if tpl is None or frame is None or frame.size == 0:
        return None
    q, x0, y0 = _quarter_gray if _quarter_gray is not None else _quarter(frame)
    # Locate at `scale` over the quarter, then score the nearby scales in a small window only
    # (one full pass per scale took ~18 ms per icon; this takes ~4 ms). A grey match whose
    # colours are wrong (another potion of the same shape) is blanked and the next one tried.
    th, tw = tpl.shape
    pad = round(_REFINE_PAD * scale)
    masked = False
    for _ in range(_COLOR_TRIES):
        coarse = anchors.find(q, tpl, [scale], min_score=-1.0)
        if coarse is None:
            return None
        wx, wy = max(0, coarse.x - pad), max(0, coarse.y - pad)
        win = q[wy:coarse.y + round(th * scale * SCALE_TOLERANCE[-1]) + pad,
                wx:coarse.x + round(tw * scale * SCALE_TOLERANCE[-1]) + pad]
        m = anchors.find(win, tpl, [scale * f for f in SCALE_TOLERANCE], ICON_MIN_SCORE)
        if m is None:
            return None
        hit = anchors.Match(m.x + wx + x0, m.y + wy + y0, m.scale, m.score)
        if _colour_ok(frame, name, hit):
            return hit
        if not masked:  # never write into the caller's shared quarter
            q, masked = q.copy(), True
        q[max(0, coarse.y - pad):coarse.y + round(th * scale) + pad,
          max(0, coarse.x - pad):coarse.x + round(tw * scale) + pad] = 0
    return None


def read_potion_counts(frame: np.ndarray, scale: float, names: list[str]) -> dict[str, int]:
    """{name: count} for the potions in `names` found on a quickslot key with a readable count.

    Potions that are not on a key, or whose count is covered or unreadable, are left out."""
    if frame is None or frame.size == 0:
        return {}
    quarter = _quarter(frame)
    out: dict[str, int] = {}
    for name in dict.fromkeys(names):
        m = find_icon(frame, scale, name, quarter)
        if m is None:
            continue
        crop = count_rect(m.x, m.y, m.scale).crop(frame)
        if crop.shape[0] < 2 or crop.shape[1] < 2:
            continue
        text = read(crop, _digits(), m.scale, "count")
        if text.isdigit():
            out[name] = int(text)
    return out


def key_origins(frame: np.ndarray, scale: float) -> list[tuple[int, int]]:
    """Top-left (frame px) of the 12 quickslot keys, row by row (top row first)."""
    fh, fw = frame.shape[:2]
    pitch = KEY_PITCH * scale
    x1 = fw - GRID_RIGHT * scale
    y1 = fh - GRID_BOTTOM * scale
    keys = [(x1 + c * pitch, y1 + (r - 1) * pitch) for r in range(3) for c in range(4)]
    quarter = _quarter(frame)
    for name in _icons():  # snap to a known icon on a key, if any
        m = find_icon(frame, scale, name, quarter)
        if m is None:
            continue
        kx, ky = m.x - ICON_DX * m.scale, m.y - ICON_DY * m.scale
        near = min(keys, key=lambda k: abs(k[0] - kx) + abs(k[1] - ky))
        dx, dy = kx - near[0], ky - near[1]
        if abs(dx) <= _SNAP * scale and abs(dy) <= _SNAP * scale:
            keys = [(x + dx, y + dy) for x, y in keys]
        break
    return [(round(x), round(y)) for x, y in keys]


def read_key_counts(frame: np.ndarray, scale: float,
                    origins: list[tuple[int, int]] | None = None) -> dict[int, int]:
    """{key index: count} for every key showing a readable count."""
    if frame is None or frame.size == 0:
        return {}
    out = {}
    for i, (kx, ky) in enumerate(origins if origins is not None else key_origins(frame, scale)):
        ix, iy = kx + round(ICON_DX * scale), ky + round(ICON_DY * scale)
        crop = count_rect(ix, iy, scale).crop(frame)
        if crop.shape[0] < 2 or crop.shape[1] < 2:
            continue
        text = read(crop, _digits(), scale, "count")
        if text.isdigit():
            out[i] = int(text)
    return out


def key_icon(frame: np.ndarray, scale: float, origin: tuple[int, int]) -> np.ndarray | None:
    """The icon template of the key at `origin` (native px, colour), as templates/potions."""
    kx, ky = origin
    x, y = kx + round(ICON_DX * scale), ky + round(ICON_DY * scale)
    crop = frame[y:y + round(ICON_H * scale), x:x + round(ICON_W * scale)]
    if crop.shape[0] < 2 or crop.shape[1] < 2:
        return None
    from expstrip.vision.glyphs import to_native
    nat = to_native(crop, scale)
    return cv2.resize(nat, (ICON_W, ICON_H), interpolation=cv2.INTER_AREA) if nat.shape[:2] != (ICON_H, ICON_W) else nat


class CountConfirmer:
    """Passes a potion's count on only when two successive reads agree, so a one-frame
    misread (e.g. a cursor over a digit) is never used."""

    def __init__(self) -> None:
        self._prev: dict[str, int] = {}

    def reset(self) -> None:
        self._prev = {}

    def feed(self, counts: dict[str, int]) -> dict[str, int]:
        out = {k: v for k, v in counts.items() if self._prev.get(k) == v}
        self._prev = dict(counts)
        return out
