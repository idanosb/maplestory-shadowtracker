"""Template-based reader for the status-bar EXP text and level digit tiles.

Pipeline (per the Task 7 brief): resize the crop to native scale, binarize, split into
glyphs at empty columns, classify each glyph by best TM_CCOEFF_NORMED against the
same-height templates, concatenate. Low-scoring or ambiguous glyphs become "?"
(see MIN_SCORE, MIN_SCORE_PUNCT, MIN_GAP).

Two refinements, both driven by the fixtures:
- The client upscales its UI, so the same character renders slightly differently
  depending on its sub-pixel position. The binary mask is only used to find glyphs;
  classification compares the soft intensity image ("ink" in 0..1), and a character
  may have several template variants (`6.png`, `6_2.png`, ...).
- The exp threshold is V > 130 (brief: 170) so anti-aliased strokes stay connected.

Mode "count" reads the quickslot item counts: white digits with a dark outline drawn over
the item icon (see _count_mask).

Mode "mesos" reads the inventory window's meso total: dark digits and commas on a white field.

Templates are extracted at native 2560x1440 by tools/extract_templates.py.
Pure image functions: no capture, no window or process access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from expstrip.paths import templates_dir

__all__ = [
    "GlyphSet", "read", "binarize", "ink", "segment", "glyph_images", "classify",
    "rank", "templates_dir", "NAME_TO_CHAR", "CHAR_TO_NAME", "MIN_SCORE", "MIN_SCORE_PUNCT", "MIN_GAP",
]

Mode = Literal["exp", "level", "count", "mesos"]

NAME_TO_CHAR = {"comma": ",", "dot": ".", "pct": "%", "lbr": "[", "rbr": "]"}
CHAR_TO_NAME = {v: k for k, v in NAME_TO_CHAR.items()}

# Acceptance rules for one glyph, chosen from leave-one-fixture-out data at scales 1.0 and
# 0.78 (see the Task 7 report). Digits are what a downstream validator cannot check, so:
# - a digit must score >= MIN_SCORE (correct digits scored >= 0.860; the brief's 0.6 is far
#   below the 0.87-0.91 that *wrong* digits reach), and
# - it must beat the best *other digit* by >= MIN_GAP (correct digits led by >= 0.060;
#   a 6/8 blend leads by ~0), otherwise it reads "?".
# Punctuation (`, . % [ ]`) only needs MIN_SCORE_PUNCT: it scored >= 0.833, and confusing it
# (`,` vs `.` led by only 0.015, `[` vs `1` by 0.002 at 0.78x) breaks the fixed
# "digits,digits[digits.digits%]" shape, so the parser rejects it instead.
MIN_SCORE = 0.85
MIN_SCORE_PUNCT = 0.75
MIN_GAP = 0.035
_SHIFT = 2  # +/- pixel slack when aligning a glyph against a template


def char_for_stem(stem: str) -> str:
    """`6` -> "6", `6_2` -> "6" (variant), `comma` -> ",", `pct_3` -> "%"."""
    base = stem.split("_", 1)[0]
    return NAME_TO_CHAR.get(base, base)


@dataclass
class GlyphSet:
    templates: dict[str, list[np.ndarray]] = field(default_factory=dict)  # char -> float32 0..1 images

    @property
    def chars(self) -> list[str]:
        return list(self.templates)

    @classmethod
    def load(cls, folder: Path) -> "GlyphSet":
        out: dict[str, list[np.ndarray]] = {}
        for p in sorted(Path(folder).glob("*.png")):
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            out.setdefault(char_for_stem(p.stem), []).append(img.astype(np.float32) / 255.0)
        return cls(out)

    @classmethod
    def load_many(cls, folders) -> "GlyphSet":
        """Templates of several folders combined (missing folders are skipped); later folders
        add variants, e.g. the bundled level digits plus digits learned at runtime."""
        out: dict[str, list[np.ndarray]] = {}
        for folder in folders:
            if folder is not None and Path(folder).is_dir():
                for ch, imgs in cls.load(folder).templates.items():
                    out.setdefault(ch, []).extend(imgs)
        return cls(out)


def _hsv(crop: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if crop.ndim == 2:
        crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    elif crop.shape[2] == 4:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return hsv[..., 0], hsv[..., 1], hsv[..., 2]


def _tile_mask(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bounding boxes of the orange level tiles (several adjacent tiles may merge)."""
    orange = ((h <= 22) & (s > 120) & (v > 150)).astype(np.uint8)
    orange = cv2.morphologyEx(orange, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    tiles = np.zeros_like(orange)
    n, _, stats, _ = cv2.connectedComponentsWithStats(orange)
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        if area >= 60 and w >= 6 and hh >= 10:
            tiles[y:y + hh, x:x + w] = 1
    return tiles


# ---- "count" mode ---------------------------------------------------------------------------
# Quickslot item counts are thin white strokes (light blue towards the bottom) enclosed by a
# dark outline, drawn left-aligned over the item icon on a light key. A stroke pixel is a
# bright, neutral or bluish pixel in a bright region that does not touch the crop border: the
# key background around the count reaches the border, the outline keeps the strokes apart from
# it - in 4-connected blobs, since the outline has diagonal gaps that would join a stroke to a
# white key background. Warm or strongly coloured pixels (the icons: yellow lemon, red meat, beige bone) are
# dropped, and so are bright patches thicker than a stroke.
#
# The strokes are broken at the glyph corners and neighbouring digits overlap in x (the "7"
# leans under the digit before it), so glyphs are not split at empty columns. Every digit
# advances COUNT_PITCH except "1" (COUNT_ADVANCE), so each cell's left edge follows from the
# first one and the digits before it; each cell is matched against same-size cell templates
# (templates/count/, one glyph per cell).
_COUNT_BRIGHT = 150       # brightest channel; strokes are ~250 (top) to ~229 (blue bottom)
_COUNT_MAX_SPREAD = 110   # max-min channel spread; the light blue fill (229,183,137 BGR) is 92
_COUNT_WARM = 10          # drop pixels whose red exceeds blue by more than this
COUNT_PITCH = 44 / 3      # native px per digit cell (11 px at 1920 wide, x 4/3)
COUNT_ADVANCE = {"1": 32 / 3}  # narrower advances (meso_a: "103", "101": the 0 follows 10-11 px on)
COUNT_CELL_W = 14         # template width: a digit is <= 12 px wide; the next one starts 14.7 px on
COUNT_PAD = 3             # rows above and below the digit band in a count crop (vertical slack)
COUNT_FIRST_X = (0, 10)   # where the first cell's left edge may sit in a count crop
COUNT_MAX_DIGITS = 4      # cells that fit on a key
_COUNT_SLACK = 2          # +/- px for every later cell around its expected left edge
_COUNT_MIN_INK = 12       # stroke pixels that make a cell non-empty (a "1" has ~25)
COUNT_MIN_SCORE = 0.75    # see the Task 4 report for the measured score distributions
COUNT_MIN_GAP = 0.05


def _bgr(crop: np.ndarray) -> np.ndarray:
    if crop.ndim == 2:
        return cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    if crop.shape[2] == 4:
        return cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR)
    return crop


def _count_mask(crop: np.ndarray) -> np.ndarray:
    c = _bgr(crop).astype(np.int16)
    hi, lo = c.max(axis=2), c.min(axis=2)
    bright = (hi > _COUNT_BRIGHT) & ((hi - lo) <= _COUNT_MAX_SPREAD) & ((c[..., 2] - c[..., 0]) <= _COUNT_WARM)
    _, lab, stats, _ = cv2.connectedComponentsWithStats(bright.astype(np.uint8), connectivity=4)
    hh, ww = bright.shape
    x, y, w, h = stats[:, 0], stats[:, 1], stats[:, 2], stats[:, 3]
    keep = (x > 0) & (y > 0) & (x + w < ww) & (y + h < hh)
    keep[0] = False
    mask = keep[lab].astype(np.uint8)
    # Strokes are 1-2 px wide; enclosed patches of key background (e.g. inside the curve of a
    # "2" next to the lemon) are thicker, so whatever a 3x3 opening keeps is not a stroke.
    return mask & (1 - cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)))


def _count_soft(native: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return np.clip((_bgr(native).max(axis=2).astype(np.float32) - 100.0) / 150.0, 0.0, 1.0) * mask


def _cell_lefts(x0: float, chars: str) -> list[int]:
    """Left edge of each cell of `chars` when the first cell starts at x0."""
    out, x = [], x0
    for ch in chars:
        out.append(int(round(x)))
        x += COUNT_ADVANCE.get(ch, COUNT_PITCH)
    return out


def count_cells(native_crop: np.ndarray, digits: str, gs: GlyphSet | None = None) -> list[np.ndarray]:
    """Soft images of the digit cells of a count crop known to read `digits`, for template
    extraction. The first cell is placed where the best template of digits[0] in `gs` matches;
    without one, 1 px left of the leftmost stroke (wrong for a leading "1", whose stroke sits
    mid-cell). Later cells follow by the advances, as in the reader."""
    mask = _count_mask(native_crop)
    soft = _count_soft(native_crop, mask)
    x0 = None
    if gs is not None and gs.templates.get(digits[0]):
        hits = [r for r in _match_cell(soft, *COUNT_FIRST_X, gs) if r[1] == digits[0]]
        if hits:
            x0 = hits[0][2]
    band = soft[COUNT_PAD:soft.shape[0] - COUNT_PAD]
    if x0 is None:
        if digits[0] == "1":
            raise ValueError("a leading 1 needs a known '1' template to place its cell")
        cols = np.flatnonzero(band.max(axis=0) > 0)
        if cols.size == 0:
            return []
        x0 = int(cols[0]) - 1
    out = []
    for a in _cell_lefts(x0, digits):
        cell = band[:, a:a + COUNT_CELL_W]
        if cell.shape[1] < COUNT_CELL_W:
            break
        out.append(cell.copy())
    return out


def _match_cell(soft: np.ndarray, xa: int, xb: int, gs: GlyphSet) -> list[tuple[float, str, int]]:
    """(score, char, x) of every template's best placement with its left edge in [xa, xb]."""
    xa = max(0, xa)
    win = soft[:, xa:xb + COUNT_CELL_W]
    out = []
    if win.std() == 0:
        return out
    for ch, variants in gs.templates.items():
        best = (-1.0, ch, xa)
        for tpl in variants:
            if tpl.shape[0] > win.shape[0] or tpl.shape[1] > win.shape[1]:
                continue
            res = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
            np.nan_to_num(res, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            _, sc, _, loc = cv2.minMaxLoc(res)
            if sc > best[0]:
                best = (float(sc), ch, xa + loc[0])
        out.append(best)
    out.sort(reverse=True)
    return out


def _read_count(native: np.ndarray, gs: GlyphSet) -> str:
    """Digits of a count crop, cell by cell from the left, until an empty cell."""
    mask = _count_mask(native)
    soft = _count_soft(native, mask)
    band = mask[COUNT_PAD:mask.shape[0] - COUNT_PAD]
    text, x0 = "", None
    for _ in range(COUNT_MAX_DIGITS):
        if x0 is None:
            xa, xb = COUNT_FIRST_X
        else:
            e = _cell_lefts(x0, text + "0")[-1]
            xa, xb = e - _COUNT_SLACK, e + _COUNT_SLACK
        if int(band[:, max(0, xa + 2):xb + COUNT_CELL_W - 2].sum()) < _COUNT_MIN_INK:
            break  # empty cell: the count ended
        ranked = _match_cell(soft, xa, xb, gs)
        if not ranked:
            return text + "?"
        sc, ch, x = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else -1.0
        if sc < COUNT_MIN_SCORE or sc - runner_up < COUNT_MIN_GAP:
            return text + "?"
        text += ch
        if x0 is None:
            x0 = x
    return text


def binarize(crop: np.ndarray, mode: Mode) -> np.ndarray:
    """uint8 0/1 mask of glyph pixels in a native-scale crop."""
    if mode == "count":
        return _count_mask(crop)
    h, s, v = _hsv(crop)
    if mode == "exp":
        white = (v > 130) & (s < 70)
        green = (h >= 25) & (h <= 50) & (s > 80) & (v > 100)  # the [ ] brackets (~#8fd63a)
        return (white | green).astype(np.uint8)
    if mode == "mesos":
        return (v < 150).astype(np.uint8)
    if mode == "level":
        light = (v > 200) & (s < 80)
        return (light & (_tile_mask(h, s, v) > 0)).astype(np.uint8)
    raise ValueError(f"unknown mode {mode!r}")


def ink(crop: np.ndarray, mode: Mode) -> np.ndarray:
    """float32 0..1 soft glyph intensity used for classification."""
    if mode == "count":
        return _count_soft(crop, _count_mask(crop))
    h, s, v = _hsv(crop)
    vf, sf = v.astype(np.float32), s.astype(np.float32)
    if mode == "exp":
        return np.clip((vf - 80.0) / 150.0, 0.0, 1.0)  # white digits and green brackets are both bright
    if mode == "mesos":
        return np.clip((240.0 - vf) / 190.0, 0.0, 1.0)  # dark strokes on the white field
    if mode == "level":
        whiteness = np.clip((vf - sf - 40.0) / 160.0, 0.0, 1.0)  # white digit vs saturated orange tile
        return whiteness * _tile_mask(h, s, v)
    raise ValueError(f"unknown mode {mode!r}")


def _line_rows(mask: np.ndarray) -> tuple[int, int]:
    """Row range of the main ink band: the tallest run of rows holding >= 2 ink pixels.

    Stray bright pixels above or below the text (or a UI border further away) then do not
    stretch the line height, which would make every glyph mismatch its template.
    """
    rows = mask.sum(axis=1) >= 2
    best, start = (0, 0), None
    for i, on in enumerate(list(rows) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


def _spans(mask: np.ndarray) -> tuple[int, int, list[tuple[int, int]]]:
    r0, r1 = _line_rows(mask)
    if r1 <= r0:
        return 0, 0, []
    line = mask[r0:r1]
    cols = line.any(axis=0)
    spans: list[tuple[int, int]] = []
    c, n = 0, cols.size
    while c < n:
        if not cols[c]:
            c += 1
            continue
        a = c
        while c < n and cols[c]:
            c += 1
        if int(line[:, a:c].sum()) >= 2:  # drop single stray pixels
            spans.append((a, c))
    return r0, r1, spans


def segment(mask: np.ndarray) -> list[np.ndarray]:
    """Split a 0/1 mask into glyphs at empty columns. Every glyph spans the line's ink rows."""
    r0, r1, spans = _spans(mask)
    return [mask[r0:r1, a:b] for a, b in spans]


def glyph_images(native_crop: np.ndarray, mode: Mode) -> list[np.ndarray]:
    """Soft glyph images (with a 1 px side margin) for each glyph found in a native-scale crop."""
    mask = binarize(native_crop, mode)
    soft = ink(native_crop, mode)
    r0, r1, spans = _spans(mask)
    w = mask.shape[1]
    return [soft[r0:r1, max(0, a - 1):min(w, b + 1)].copy() for a, b in spans]


def _pad(img: np.ndarray, h: int, w: int) -> np.ndarray:
    out = np.zeros((h, w), np.float32)
    y = (h - img.shape[0]) // 2
    x = (w - img.shape[1]) // 2
    out[y:y + img.shape[0], x:x + img.shape[1]] = img
    return out


def score(glyph: np.ndarray, tpl: np.ndarray) -> float:
    """Best TM_CCOEFF_NORMED of `tpl` against `glyph` within +/- _SHIFT px."""
    h = max(glyph.shape[0], tpl.shape[0])
    w = max(glyph.shape[1], tpl.shape[1])
    a = _pad(glyph.astype(np.float32), h + 2 * _SHIFT, w + 2 * _SHIFT)
    b = _pad(tpl.astype(np.float32), h, w)
    if b.std() == 0 or a.std() == 0:
        return 0.0
    res = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
    np.nan_to_num(res, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    return float(res.max())


def rank(glyph: np.ndarray, gs: GlyphSet) -> dict[str, float]:
    """Best score per character over its variants (same-height templates only)."""
    g = glyph.astype(np.float32)
    out: dict[str, float] = {}
    for ch, variants in gs.templates.items():
        for tpl in variants:
            th = tpl.shape[0]
            dh = abs(th - g.shape[0])
            if dh > max(_SHIFT, round(0.15 * th)):
                continue  # only same-height templates are comparable
            gg = g
            if dh > _SHIFT:  # small differences (e.g. a clipped bottom row) are absorbed by the shift slack
                nw = max(1, round(g.shape[1] * th / g.shape[0]))
                gg = cv2.resize(g, (nw, th), interpolation=cv2.INTER_LINEAR)
            sc = score(gg, tpl)
            if sc > out.get(ch, -1.0):
                out[ch] = sc
    return out


def classify(glyph: np.ndarray, gs: GlyphSet) -> tuple[str, float]:
    """(char, score). The char is "?" when the best match is below its floor (MIN_SCORE for
    digits, MIN_SCORE_PUNCT otherwise) or, for a digit, when another digit is within MIN_GAP."""
    ranked = sorted(rank(glyph, gs).items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return "?", -1.0
    best_ch, best = ranked[0]
    if not best_ch.isdigit():
        return (best_ch, best) if best >= MIN_SCORE_PUNCT else ("?", best)
    other_digit = max((v for k, v in ranked[1:] if k.isdigit()), default=-1.0)
    if best < MIN_SCORE or best - other_digit < MIN_GAP:
        return "?", best
    return best_ch, best


def to_native(crop: np.ndarray, scale: float) -> np.ndarray:
    if crop.size == 0 or abs(scale - 1.0) < 1e-6:
        return crop
    f = 1.0 / scale
    h, w = crop.shape[:2]
    size = (max(1, round(w * f)), max(1, round(h * f)))
    return cv2.resize(crop, size, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)


_SPLIT_BELOW = MIN_SCORE  # try splitting a segment that matches worse than a confident digit
_SPLIT_FLOOR = MIN_SCORE_PUNCT  # every part of an accepted split must reach at least this
_SPLIT_GAIN = 0.05  # a split must beat the unsplit score by this much
_SPLIT_MAX_GLYPHS = 6  # segments wider than this many glyphs are not split


def _read_segment(g: np.ndarray, gs: GlyphSet, min_w: int, max_w: int, depth: int = 0) -> list[tuple[str, float]]:
    """Classify one segment; if it looks like touching glyphs (common after rescaling), split it."""
    ch, sc = classify(g, gs)
    w = g.shape[1]
    if depth >= 3 or w < 2 * min_w or (sc >= _SPLIT_BELOW and w <= max_w + 2):
        return [(ch, sc)]
    if w > _SPLIT_MAX_GLYPHS * max_w:
        return [(ch, sc)]  # far too wide to be touching glyphs; don't search
    # Candidate cuts: columns where the ink profile has a local minimum (where glyphs touch).
    col = g.sum(axis=0)
    cuts = [c for c in range(min_w - 1, w - min_w + 1)
            if col[c] <= col[max(0, c - 1)] and col[c] <= col[min(w - 1, c + 1)]]
    best: list[tuple[str, float]] | None = None
    best_min = sc + _SPLIT_GAIN
    for c in cuts:
        left = classify(g[:, :c + 1], gs)  # column c is shared as a 1 px margin
        if left[1] < max(_SPLIT_FLOOR, best_min):
            continue
        right = _read_segment(g[:, c:], gs, min_w, max_w, depth + 1)
        worst = min([left[1]] + [r[1] for r in right])
        if worst >= max(_SPLIT_FLOOR, best_min):
            best, best_min = [left] + right, worst
    return best if best is not None else [(ch, sc)]


def read(crop: np.ndarray, gs: GlyphSet, scale: float, mode: Mode) -> str:
    """Read the text in `crop` (frame pixels at `scale` x native). Unknown glyphs become "?"."""
    if crop is None or crop.size == 0 or not gs.templates:
        return ""
    native = to_native(crop, scale)
    if mode == "count":
        return _read_count(native, gs)
    widths = [t.shape[1] for v in gs.templates.values() for t in v]
    min_w, max_w = max(2, min(widths)), max(widths)
    out = []
    for g in glyph_images(native, mode):
        out.extend(ch for ch, _ in _read_segment(g, gs, min_w, max_w))
    return "".join(out)
