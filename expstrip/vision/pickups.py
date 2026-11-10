"""Read the game's pickup notifications ("You have gained mesos (+12)") and count each once.

The lines sit at the bottom-right of the client, right-aligned, one line per notification.
New lines appear at the bottom; older ones move up one line height (about 26.5 px at scale
1.0) per new line and fade out. Offsets are native px at scale 1.0 (see templates/README.md).

The client draws these lines in a fixed pixel font, white with a dark outline and alpha-faded
over a moving background. Windows OCR misread even fresh lines (79 as 70/73/713, 80 as 813),
so lines are read with templates instead (templates/pickup/, extracted from recordings by
tools/extract_pickup_templates.py):
1. "ink" = white-tophat of the darkest channel: thin bright strokes stay, wide bright
   backgrounds go; strongly coloured pixels are dropped.
2. The fixed prefix "...gained mesos (+" is found with TM_CCOEFF_NORMED, which ignores the
   gain and offset of faded lines. Lines of other kinds (EXP, items) don't match it.
3. The amount is read glyph by glyph right of the prefix until ")".
Pure image functions: no capture, no window access.
"""
from __future__ import annotations

import re

import cv2
import numpy as np

from expstrip.paths import templates_dir
from expstrip.vision.glyphs import to_native
from expstrip.vision.layout import Rect

# ---- region (native px at scale 1.0, measured from the client's bottom-right corner) -------
RIGHT_INSET = 2      # right edge of the region, left of the client's right edge
REGION_W = 422       # long enough for "gained mesos (+1,234,567)"
BOTTOM_INSET = 276   # bottom edge of the region, above the client's bottom edge
REGION_H = 242       # about 9 line slots

# ---- reader (native px) ---------------------------------------------------------------------
LINE_H = 22          # template height: one text line incl. parentheses and descenders
PREFIX_W = 150       # prefix template width, ending at the "+" of "(+"
_TOPHAT_K = 5        # strokes are 1-2 px wide; a 5x5 opening removes them
_MAX_SAT = 60        # text is white (alpha-blended); strongly coloured pixels are background
PREFIX_MIN = 0.55    # prefix match needed to call a line a meso pickup
GLYPH_MIN = 0.75     # each glyph of the amount must match at least this ...
GLYPH_GAP = 0.085    # ... and beat every other character by this, else the line is skipped.
                     # On meso_b, faded lines misread their leading digit (41->11, 81->31)
                     # at 0.66-0.80 or with a lead <= 0.07; correct reads score > 0.85, and a
                     # faded "0" leads "8" by only ~0.09.
_GLYPH_DX = (-1, 5)  # where the next glyph's left edge may sit, from the previous glyph's end
_MAX_GLYPHS = 12

_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d+)"
_PICKUP_RE = re.compile(r"(?:gained|m?[ec]sos)\D*\(\s*\+\s*" + _AMOUNT + r"\s*\)", re.IGNORECASE)
_NAME_TO_CHAR = {"rpar": ")", "comma": ","}  # no comma template yet: amounts >= 1,000 are skipped


def parse_pickup(text: str) -> int | None:
    """The amount of a 'You have gained mesos (+N)' line, else None."""
    m = _PICKUP_RE.search(text)
    return int(m.group(1).replace(",", "")) if m else None


def pickup_rect(frame: np.ndarray, scale: float) -> Rect | None:
    """The pickup-line region of a client frame, anchored to its bottom-right corner."""
    fh, fw = frame.shape[:2]
    x1, y1 = fw - round(RIGHT_INSET * scale), fh - round(BOTTOM_INSET * scale)
    x0, y0 = max(0, x1 - round(REGION_W * scale)), max(0, y1 - round(REGION_H * scale))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return Rect(x0, y0, x1 - x0, y1 - y0)


def ink(crop: np.ndarray) -> np.ndarray:
    """float32 stroke intensity of a native-scale BGR crop (white text strokes high)."""
    s = crop.astype(np.int16)
    lo = s.min(axis=2)
    th = cv2.morphologyEx(lo.astype(np.uint8), cv2.MORPH_TOPHAT,
                          np.ones((_TOPHAT_K, _TOPHAT_K), np.uint8)).astype(np.float32)
    th[(s.max(axis=2) - lo) > _MAX_SAT] = 0
    return th


_TPL: dict | None = None


def _templates() -> tuple[np.ndarray, list[tuple[str, np.ndarray]]]:
    global _TPL
    if _TPL is None:
        folder = templates_dir() / "pickup"
        prefix = cv2.imread(str(folder / "prefix.png"), cv2.IMREAD_GRAYSCALE)
        if prefix is None:
            raise FileNotFoundError(folder / "prefix.png")
        glyphs = []
        for p in sorted(folder.glob("*.png")):
            if p.stem != "prefix":
                base = p.stem.split("_", 1)[0]
                glyphs.append((_NAME_TO_CHAR.get(base, base), cv2.imread(str(p), cv2.IMREAD_GRAYSCALE).astype(np.float32)))
        _TPL = {"prefix": prefix.astype(np.float32), "glyphs": glyphs}
    return _TPL["prefix"], _TPL["glyphs"]


def _match(img: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    return np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)


def _read_amount(im: np.ndarray, x: int, y: int, glyphs) -> int | None:
    """Digits right of a prefix ending at column x (line top row y), up to the ")"."""
    text = ""
    y0, y1 = max(0, y - 1), min(im.shape[0], y + LINE_H + 1)
    for _ in range(_MAX_GLYPHS):
        scores: dict[str, tuple[float, int]] = {}
        for ch, tpl in glyphs:
            w = tpl.shape[1]
            xa, xb = max(0, x + _GLYPH_DX[0]), min(im.shape[1], x + _GLYPH_DX[1] + w)
            win = im[y0:y1, xa:xb]
            if win.shape[0] < tpl.shape[0] or win.shape[1] < w:
                continue
            _, sc, _, loc = cv2.minMaxLoc(_match(win, tpl))
            if sc > scores.get(ch, (-1.0, 0))[0]:
                scores[ch] = (sc, xa + loc[0] + w - 1)  # glyph templates carry a 1 px margin
        ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
        if not ranked:
            return None
        ch, (sc, end) = ranked[0]
        runner_up = ranked[1][1][0] if len(ranked) > 1 else -1.0
        if sc < GLYPH_MIN or sc - runner_up < GLYPH_GAP:
            return None
        if ch == ")":
            return parse_pickup(f"mesos (+{text})")
        text += ch
        x = end
    return None


def read_pickup_lines(crop: np.ndarray, scale: float) -> list[tuple[int, float]]:
    """Meso pickups in a pickup_rect crop: (amount, line center y in native px), top to bottom.

    Lines of other kinds (EXP, items, quests) and unreadable lines are left out.
    """
    if crop is None or crop.size == 0:
        return []
    prefix, glyphs = _templates()
    im = ink(to_native(crop, scale))
    if im.shape[0] < prefix.shape[0] or im.shape[1] < prefix.shape[1]:
        return []
    res = _match(im, prefix)
    out = []
    while True:
        _, sc, _, (x, y) = cv2.minMaxLoc(res)
        if sc < PREFIX_MIN:
            break
        res[max(0, y - LINE_H // 2):y + LINE_H // 2 + 1, :] = 0  # one line per row band
        amount = _read_amount(im, x + PREFIX_W, y, glyphs)
        if amount is not None:
            out.append((amount, y + LINE_H / 2))
    out.sort(key=lambda t: t[1])
    return out


def region_changed(prev: np.ndarray | None, cur: np.ndarray) -> bool:
    """Cheap check whether the pickup region differs enough from the last crop that was read.

    Compares the brightest channel at half resolution; >= 20 sampled pixels changing by
    more than 40 levels (one new text line changes hundreds) counts as changed.
    """
    if prev is None or prev.shape != cur.shape:
        return True
    a = prev[::2, ::2].max(axis=2).astype(np.int16)
    b = cur[::2, ::2].max(axis=2).astype(np.int16)
    return int((np.abs(a - b) > 40).sum()) >= 20


class PickupCounter:
    """Turns successive line lists into new pickups, counting each line exactly once.

    A line is (key, y) or (key, y, level). The key is anything compared by equality: a meso
    amount, an items.ItemLine, ... Level is the line's brightness (optional). Lines only
    fade, so a line clearly brighter than a remembered line with its key can't be that line:
    a track keeps its line's level at its last read, lowered by FADE per frame since, and a
    read above that x RISE + RISE_ABS doesn't match it. A read below COVERED x that bound
    (a mob or effect over the line) keeps the bound instead of the read. On loot_a this told a new
    Tablecloth from the one the stack had just scrolled past (frames 114, 172, 243, 246).

    The counter remembers each line ("track": key, y) for up to MAX_AGE frames after it
    was last read, so a line that is covered, faded or rejected as weak for a frame or two is
    still recognised when it is read again. Per frame:
    - All tracks and the current lines are aligned by one common upward shift d (lines only
      move up). Candidates are 0 and every y difference between equal keys, so no line
      pitch is assumed. A current line with a track's key at the track's y - d (within
      TOL), and not brighter than its level bound allows, is old. The d wins that
      matches the most lines read in the previous frame, then the most tracks in all, then
      leaves the fewest lines ignored, then is smallest. (An older track is not moved while
      unseen lines scroll, so a match against it is weaker evidence for d: meso_a frame 287.)
    - Unmatched lines below every shifted line of the previous frame are new; other unmatched
      lines (misread or briefly hidden old lines) are ignored. Only the previous frame's
      lines bound this: older tracks are not moved while unseen lines scroll, so their y is
      stale.
    - If no track matches at any d, the old lines have faded and every current line is new.
      This includes a lone line at the y of a just-read line with another amount: on meso_a
      frame 109 that was a real new +74 in the slot the +79 had just left (indistinguishable
      by position from a 79 -> 74 misread), and after the glyph confidence filter no
      confident misread occurred on meso_b. A misread transient would therefore add its
      value once, but the correct read that follows matches the remembered line and is not
      counted again.
    - Tracks not read this frame move with d and age; after MAX_AGE frames they are dropped.
    """

    TOL = 6.0     # native px
    MAX_AGE = 3   # frames a line is remembered after it was last read (lines stay about 6 s)
    # Level bound. On loot_a a line's level (fresh 150-235) fell by 30-45 per second until it
    # was gone below ~20; no read of the same line rose above the previous read.
    FADE = 15.0      # per frame (1 s) since the track was last read
    RISE = 1.05
    RISE_ABS = 10.0
    COVERED = 0.5    # a read below this fraction of the bound is taken as covered, not faded

    def __init__(self) -> None:
        self._tracks: list[list] = []  # [key, y, age, level or None]

    def reset(self) -> None:
        self._tracks = []

    def _bound(self, track) -> float | None:
        """Upper bound of a track's level now (it was last read age + 1 frames ago)."""
        return None if track[3] is None else max(0.0, track[3] - self.FADE * (track[2] + 1))

    def _level(self, level, bound):
        """Level to remember for a read matched to a track with this bound."""
        if level is None or bound is None:
            return level
        return bound if level < self.COVERED * bound else level

    def _brighter(self, level, track) -> bool:
        b = self._bound(track)
        return level is not None and b is not None and level > b * self.RISE + self.RISE_ABS

    def _match(self, cur, d: float) -> dict[int, int]:
        matched: dict[int, int] = {}
        used: set[int] = set()
        for i, (a, y, lv) in enumerate(cur):
            best = None
            for j, (ta, ty, _, _) in enumerate(self._tracks):
                if j in used or ta != a or self._brighter(lv, self._tracks[j]):
                    continue
                err = abs(ty - d - y)
                if err <= self.TOL and (best is None or err < best[0]):
                    best = (err, j)
            if best is not None:
                matched[i] = best[1]
                used.add(best[1])
        return matched

    def _age(self, d: float, keep: set[int]) -> list[list]:
        return [[a, y - d, age + 1, lv] for j, (a, y, age, lv) in enumerate(self._tracks)
                if j not in keep and age + 1 <= self.MAX_AGE]

    def feed(self, lines: list[tuple]) -> list:
        cur = sorted(((t[0], t[1], t[2] if len(t) > 2 else None) for t in lines), key=lambda t: t[1])
        if not cur:
            self._tracks = self._age(0.0, set())
            return []
        if not self._tracks:
            self._tracks = [[a, y, 0, lv] for a, y, lv in cur]
            return [a for a, _, _ in cur]
        # Only lines read in the previous frame bound where new lines may appear: older tracks
        # are not moved while unseen lines scroll, so their y is stale.
        recent = [ty for _, ty, age, _ in self._tracks if age == 0]
        shifts = {0.0} | {ty - y for ta, ty, _, _ in self._tracks for a, y, _ in cur if ta == a and ty - y > -self.TOL}
        best = None
        for d in sorted(shifts):
            m = self._match(cur, d)
            if not m:
                continue
            low = max([cur[i][1] for i in m] + [ty - d + self.TOL for ty in recent])
            new = [i for i, (_, y, _) in enumerate(cur) if i not in m and y > low]
            stray = len(cur) - len(m) - len(new)
            key = (sum(1 for j in m.values() if self._tracks[j][2] == 0), len(m), -stray, -d)
            if best is None or key > best[0]:
                best = (key, d, m, new)
        if best is None:  # nothing matches: the old lines faded, everything is new
            d, m, new = 0.0, {}, list(range(len(cur)))
        else:
            _, d, m, new = best
        bounds = {i: self._bound(self._tracks[j]) for i, j in m.items()}
        self._tracks = ([[a, y, 0, self._level(lv, bounds.get(i))] for i, (a, y, lv) in enumerate(cur)]
                        + self._age(d, set(m.values())))
        return [cur[i][0] for i in new]
