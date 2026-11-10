"""Multi-scale template matching for fixed UI landmarks (EXP label, LV label, minimap bar).

Pure image functions: no capture, no window or process access.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

DEFAULT_SCALES = np.arange(0.45, 2.01, 0.05)  # 1280x720 (0.5) .. 4K and up

# Coarse pass: each scale is scored on the frame shrunk by an integer factor d chosen so the
# template keeps at least _COARSE_MIN_SIDE px on its short side; the best _REFINE_CANDIDATES
# hits are then re-scored at full resolution in a small window.
_COARSE_MIN_SIDE = 7
_COARSE_MAX_FACTOR = 4
_REFINE_CANDIDATES = 5


@dataclass(frozen=True)
class Match:
    x: int  # top-left of the match, frame pixels
    y: int
    scale: float  # template scale relative to native 2560x1440 captures
    score: float


def _gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _resize(img: np.ndarray, fx: float) -> np.ndarray:
    h, w = img.shape[:2]
    size = (max(1, round(w * fx)), max(1, round(h * fx)))
    interp = cv2.INTER_AREA if fx < 1 else cv2.INTER_LINEAR
    return cv2.resize(img, size, interpolation=interp)


def _best(img: np.ndarray, tpl: np.ndarray) -> tuple[float, tuple[int, int]]:
    if tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
        return -1.0, (0, 0)
    res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    np.nan_to_num(res, copy=False, nan=-1.0, posinf=-1.0, neginf=-1.0)
    _, mx, _, loc = cv2.minMaxLoc(res)
    return float(mx), loc


def find(
    frame: np.ndarray,
    template: np.ndarray,
    scales: Iterable[float] = DEFAULT_SCALES,
    min_score: float = 0.8,
) -> Match | None:
    """Best TM_CCOEFF_NORMED match of `template` in `frame` over `scales`, or None below `min_score`.

    Coarse-to-fine: every scale is scored on a downsampled frame, then the best few
    candidates are re-scored at full resolution in a small window around the coarse hit.
    """
    f = _gray(frame)
    t = _gray(template)
    scales = sorted({round(float(s), 4) for s in scales})
    if not scales or t.size == 0 or f.size == 0:
        return None

    pyramid: dict[int, np.ndarray] = {1: f}
    coarse = []
    for sc in scales:
        d = int(max(1, min(_COARSE_MAX_FACTOR, min(t.shape) * sc // _COARSE_MIN_SIDE)))
        if d not in pyramid:
            pyramid[d] = _resize(f, 1.0 / d)
        score, (cx, cy) = _best(pyramid[d], _resize(t, sc / d))
        coarse.append((score, sc, cx * d, cy * d, d))
    coarse.sort(reverse=True)

    best: Match | None = None
    for _, sc, cx, cy, d in coarse[:_REFINE_CANDIDATES]:
        ts = _resize(t, sc)
        th, tw = ts.shape
        margin = 2 * d + 2
        x0, y0 = max(0, cx - margin), max(0, cy - margin)
        x1, y1 = min(f.shape[1], cx + tw + margin), min(f.shape[0], cy + th + margin)
        score, (lx, ly) = _best(f[y0:y1, x0:x1], ts)
        if best is None or score > best.score:
            best = Match(int(x0 + lx), int(y0 + ly), sc, score)
    if best is None or best.score < min_score:
        return None
    return best
