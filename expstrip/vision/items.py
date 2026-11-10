"""Read the game's item pickup lines ("Tablecloth x1 earned. (Etc)") in the pickup stack.

The lines share the stack, the region (pickups.pickup_rect) and the font of the meso lines:
right-aligned, white with a dark outline, alpha-faded. They are read with templates on the
same "ink" image as the meso lines (templates/items/, cut from recordings by
tools/extract_item_templates.py):
1. The fixed right-aligned suffix " earned. (Etc)" / " earned. (Use)" is found with
   TM_CCOEFF_NORMED; it gives the line's row and its tab. EXP, meso and quest lines don't
   match it.
2. The quantity is read glyph by glyph right to left from the suffix, back to the "x": the
   meso digit templates plus digits cut from item lines (`qty1.png`; the item lines' "1"
   renders a little differently from the meso lines' one).
3. The name is the best of the per-name templates (`<visible name>.png`, `_n` variants)
   ending just left of the "x". A name cut with "..." has a template of its visible text
   (`Animal Fur Bun....png`) and is resolved by match_name. A line whose name has no
   template goes to the optional `fallback` (expstrip.vision.item_names.NameLearner: Windows
   OCR matched against the price list, which also learns a template for the name), else it
   is unreadable and left out.
Pure image functions: no capture, no window access (the fallback is the caller's).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from expstrip.paths import templates_dir
from expstrip.vision import pickups
from expstrip.vision.glyphs import to_native
from expstrip.vision.pickups import LINE_H, ink

# ---- geometry (native px at scale 1.0, measured on recordings/loot_a) ------------------------
# Suffix templates span from 1 px left of the "e" of "earned" to 1 px right of the final ")".
SUFFIX_W = {"Etc": 130, "Use": 141}
# Glyph windows give where a glyph cell's right edge (exclusive, incl. the 1 px margin) may
# sit, relative to the left edge of the cell right of it (or of the suffix).
_QTY_RIGHT = (-12, -2)   # last qty digit, from the suffix start
_DIGIT_RIGHT = (-3, 1)   # a digit, from the digit right of it (13 px pitch, 12 px cells)
_X_RIGHT = (-4, 4)       # the "x", from the first digit (a "1" leaves its cell's left blank)
NAME_GAP = 5           # a name template ends this many px left of the "x" cell's start
_NAME_DX = 3           # +/- px slack for the name's right edge
_MAX_QTY_DIGITS = 3

# ---- thresholds ------------------------------------------------------------------------------
SUFFIX_MIN = 0.55      # suffix match needed to call a line an item line
GLYPH_MIN = 0.60       # each qty digit must match at least this ...
GLYPH_GAP = 0.12       # ... and beat every other candidate by this (on loot_a a faded or
                       # half-covered "1" scored 0.50-0.78 and still led "4" by >= 0.15)
X_MIN = 0.35           # the "x" ends the qty when it is the best candidate and scores this
NAME_MIN = 0.55        # the best name template must match at least this ...
NAME_GAP_MIN = 0.05    # ... and beat the best template of every other name by this
CUT_MIN_LETTERS = 6    # a name cut with "..." needs this many visible letters
_INK_ON = 0.35         # a column holds a stroke when some pixel reaches this share of the line's ink
_WORD_GAP = 9          # blank columns between two words of one name are at most this many

TABS = ("Etc", "Use")


@dataclass(frozen=True)
class ItemLine:
    name: str
    qty: int
    tab: str  # "Etc" | "Use"


def match_name(visible: str, names: list[str]) -> str | None:
    """The price-list name a visible line name stands for, else None.

    A full name must be in `names`. A name cut with "..." matches by its visible start,
    only when that start has >= CUT_MIN_LETTERS letters and exactly one name begins with it.
    """
    visible = visible.strip()
    if not visible.endswith("..."):
        return visible if visible in names else None
    start = visible[:-3].rstrip()
    if sum(c.isalpha() for c in start) < CUT_MIN_LETTERS:
        return None
    hits = [n for n in names if n.startswith(start)]
    return hits[0] if len(hits) == 1 else None


_VARIANT = re.compile(r"^(.*)_(\d+)$")
_QTY_DIGIT = re.compile(r"^qty\d$")
_TPL: dict | None = None
_EXTRA_DIRS: list[Path] = []   # learned name templates (data/glyphs/items), names only


def add_template_dir(folder: Path) -> None:
    """Also load name templates from `folder` (learned at runtime)."""
    folder = Path(folder)
    if folder not in _EXTRA_DIRS:
        _EXTRA_DIRS.append(folder)
        reload_templates()


def has_name_template(visible: str) -> bool:
    return any(v == visible for v, _ in _templates()["names"])


def _load(path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(path)
    return img.astype(np.float32)


def _templates() -> dict:
    """{"suffix": {tab: img}, "x": [img], "digits": [(ch, img)], "names": [(visible, img)]}"""
    global _TPL
    if _TPL is None:
        folder = templates_dir() / "items"
        suffix = {tab: _load(folder / f"suffix_{tab}.png") for tab in TABS}
        xs, digits, names = [], [], []
        for p in sorted(folder.glob("*.png")):
            stem = p.name[:-4]
            if stem.startswith("suffix_"):
                continue
            m = _VARIANT.match(stem)
            base = m.group(1) if m else stem
            if base == "x":
                xs.append(_load(p))
            elif _QTY_DIGIT.match(base):
                digits.append((base[3], _load(p)))
            else:
                names.append((base, _load(p)))
        for extra in _EXTRA_DIRS:
            for p in sorted(extra.glob("*.png")) if extra.is_dir() else []:
                m = _VARIANT.match(p.name[:-4])
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    names.append((m.group(1) if m else p.name[:-4], img.astype(np.float32)))
        _TPL = {"suffix": suffix, "x": xs, "digits": digits, "names": names}
    return _TPL


def reload_templates() -> None:
    """Forget cached templates (after tools/extract_item_templates.py wrote new ones)."""
    global _TPL
    _TPL = None


_match = pickups._match


def _best_in(im: np.ndarray, y: int, x_right: tuple[int, int], tpl: np.ndarray) -> tuple[float, int]:
    """Best score of `tpl` whose right edge (exclusive) lies in x_right, rows y-1..y+LINE_H+1.

    Returns (score, left column of the match).
    """
    w = tpl.shape[1]
    xa, xb = max(0, x_right[0] - w), min(im.shape[1], x_right[1])
    y0, y1 = max(0, y - 1), min(im.shape[0], y + tpl.shape[0] + 1)
    win = im[y0:y1, xa:xb]
    if win.shape[0] < tpl.shape[0] or win.shape[1] < w:
        return -1.0, 0
    _, sc, _, loc = cv2.minMaxLoc(_match(win, tpl))
    return sc, xa + loc[0]


def _read_qty(im: np.ndarray, sx: int, y: int, digits, xs) -> tuple[int, int] | None:
    """Quantity left of a suffix starting at column sx: (qty, left column of the "x" cell)."""
    text = ""
    left = sx
    for _ in range(_MAX_QTY_DIGITS + 1):
        dr = _QTY_RIGHT if not text else _DIGIT_RIGHT
        best: dict[str, tuple[float, int]] = {}
        cands = [(ch, tpl, dr) for ch, tpl in digits] if len(text) < _MAX_QTY_DIGITS else []
        if text:
            cands += [("x", tpl, _X_RIGHT) for tpl in xs]
        for ch, tpl, (a, b) in cands:
            sc, at = _best_in(im, y, (left + a, left + b), tpl)
            if sc > best.get(ch, (-1.0, 0))[0]:
                best[ch] = (sc, at)
        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
        if not ranked:
            return None
        ch, (sc, at) = ranked[0]
        runner_up = ranked[1][1][0] if len(ranked) > 1 else -1.0
        if ch == "x" and sc >= X_MIN:
            return int(text), at
        if ch == "x" or sc < GLYPH_MIN or sc - runner_up < GLYPH_GAP:
            return None
        text = ch + text
        left = at
    return None


def _read_name(im: np.ndarray, x_left: int, y: int, names) -> str | None:
    """Visible name of the best name template ending NAME_GAP px left of the "x" cell."""
    end = x_left - NAME_GAP
    best: dict[str, float] = {}
    for visible, tpl in names:
        sc, _ = _best_in(im, y, (end - _NAME_DX, end + _NAME_DX + 1), tpl)
        if sc > best.get(visible, -1.0):
            best[visible] = sc
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return None
    visible, sc = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
    if sc < NAME_MIN or sc - runner_up < NAME_GAP_MIN:
        return None
    return visible


def name_image(im: np.ndarray, x_left: int, y: int) -> np.ndarray | None:
    """The name of an item line as a 0..1 ink strip, LINE_H rows, ending NAME_GAP px left of
    the "x" cell (the same cut as tools/extract_item_templates.py), else None."""
    strip = im[y:y + LINE_H]
    if strip.shape[0] < LINE_H:
        return None
    strip = np.clip(strip / max(1.0, float(np.percentile(strip, 99.5))), 0, 1)
    end = x_left - NAME_GAP
    if end <= 2:
        return None
    on = (strip[:, :end] > _INK_ON).any(axis=0)
    cols = np.flatnonzero(on)
    if cols.size == 0:
        return None
    left, last = cols[-1], cols[-1]
    for c in cols[::-1]:  # walk left over the words, stop at a gap wider than a word gap
        if last - c > _WORD_GAP + 1:
            break
        left, last = c, c
    return strip[:, max(0, left - 1):end]


def _suffix_hits(im: np.ndarray, suffix: dict) -> list[tuple[int, int, str]]:
    """(row, suffix start column, tab) of every suffix match, best tab per line."""
    hits: list[tuple[float, int, int, str]] = []
    for tab, tpl in suffix.items():
        if im.shape[0] < tpl.shape[0] or im.shape[1] < tpl.shape[1]:
            continue
        res = _match(im, tpl)
        while True:
            _, sc, _, (x, y) = cv2.minMaxLoc(res)
            if sc < SUFFIX_MIN:
                break
            res[max(0, y - LINE_H // 2):y + LINE_H // 2 + 1, :] = 0
            hits.append((sc, y, x, tab))
    hits.sort(reverse=True)
    out: list[tuple[int, int, str]] = []
    for sc, y, x, tab in hits:  # "(Etc)" and "(Use)" lines look alike: keep the better per row
        if all(abs(y - oy) > LINE_H // 2 for oy, _, _ in out):
            out.append((y, x, tab))
    return out


def read_item_lines(crop: np.ndarray, scale: float, names: list[str],
                    with_level: bool = False,
                    fallback: Callable[[np.ndarray, list[str]], str | None] | None = None) -> list[tuple]:
    """Item lines in a pickup_rect crop: (line, line center y in native px), top to bottom.

    Only names in `names` are returned (cut names by match_name). Unknown, unreadable and
    non-item lines are left out; a line no name template reads goes to `fallback(name_image,
    names)` when given, which returns a name from `names` or None. With with_level, each entry is (line, y, level): the line's
    brightness (99th percentile of its suffix ink), which only falls as the line fades; it
    lets PickupCounter tell a new line from a remembered one with the same key.
    """
    if crop is None or crop.size == 0:
        return []
    tpl = _templates()
    digits = [(ch, g) for ch, g in pickups._templates()[1] if ch.isdigit()] + tpl["digits"]
    im = ink(to_native(crop, scale))
    out = []
    for y, sx, tab in _suffix_hits(im, tpl["suffix"]):
        q = _read_qty(im, sx, y, digits, tpl["x"])
        if q is None:
            continue
        qty, x_left = q
        visible = _read_name(im, x_left, y, tpl["names"])
        name = match_name(visible, names) if visible else None
        if visible is None and fallback is not None:
            img = name_image(im, x_left, y)
            name = fallback(img, names) if img is not None else None
            if name not in names:
                name = None
        if name is not None and qty > 0:
            line = (ItemLine(name, qty, tab), y + LINE_H / 2)
            if with_level:
                band = im[y:y + LINE_H, sx:sx + SUFFIX_W[tab]]
                line += (float(np.percentile(band, 99)),)
            out.append(line)
    out.sort(key=lambda t: t[1])
    return out
