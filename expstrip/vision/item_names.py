"""Read item names that have no template yet, and learn a template for them.

An item line whose name matches no template (expstrip.vision.items) is handed here as its
0..1 name strip. It is read with Windows OCR and accepted only when the text equals a name
from the price list (after light normalisation: case, spaces, "VV" for "W"), so a misread
never credits a wrong item. Fresh lines read well; faded ones come out as noise and are
simply not matched (probe: tools/ocr_names_probe.py).

When the same name has been read this way LEARN_AFTER times, its strip is saved as
<learned_dir>/<name>.png. From then on the template reads it (no OCR), exactly like the
templates cut by tools/extract_item_templates.py.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from expstrip.vision import items

log = logging.getLogger(__name__)

LEARN_AFTER = 2          # OCR reads of a name before its template is saved
_BAD_FILE_CHARS = re.compile(r'[<>:"/\\|?*]')


def normalise(text: str) -> str:
    t = text.replace("VV", "W").replace("vv", "w")
    t = re.sub(r"\s+", " ", t).strip().strip(",'`:;_-").strip()
    return t.lower()


def match_ocr(text: str, names: list[str]) -> str | None:
    """The price-list name an OCR text stands for (exact after normalise), else None.
    A text cut with "..." matches as items.match_name does, ignoring case."""
    t = normalise(text)
    if not t:
        return None
    by_norm = {normalise(n): n for n in names}
    if t in by_norm:
        return by_norm[t]
    if t.endswith("..."):
        start = t[:-3].rstrip()
        if sum(c.isalpha() for c in start) >= items.CUT_MIN_LETTERS:
            hits = [n for k, n in by_norm.items() if k.startswith(start)]
            if len(hits) == 1:
                return hits[0]
    return None


def ocr_image(strip: np.ndarray) -> np.ndarray:
    """A 0..1 ink strip as black-on-white BGR with a margin (what the OCR reads best)."""
    g = 255 - np.clip(strip * 255, 0, 255).astype(np.uint8)
    g = cv2.copyMakeBorder(g, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def _default_ocr(img: np.ndarray) -> list[str]:
    from expstrip.vision.ocr import ocr_lines
    return ocr_lines(img)


class NameLearner:
    """items.read_item_lines fallback: OCR a name strip, match it, learn its template."""

    def __init__(self, learned_dir: Path | None,
                 ocr: Callable[[np.ndarray], list[str]] = _default_ocr) -> None:
        self._dir = Path(learned_dir) if learned_dir is not None else None
        self._ocr = ocr
        self._seen: dict[str, int] = {}
        self.learned: list[str] = []
        if self._dir is not None:
            items.add_template_dir(self._dir)

    def __call__(self, strip: np.ndarray, names: list[str]) -> str | None:
        try:
            text = " ".join(self._ocr(ocr_image(strip)))
        except Exception:
            log.exception("item name OCR failed")
            return None
        name = match_ocr(text, names)
        if name is not None:
            self._offer(name, strip)
        return name

    def _offer(self, name: str, strip: np.ndarray) -> None:
        if self._dir is None or _BAD_FILE_CHARS.search(name) or items.has_name_template(name):
            return
        self._seen[name] = self._seen.get(name, 0) + 1
        if self._seen[name] < LEARN_AFTER:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{name}.png"
            if not cv2.imwrite(str(path), (np.clip(strip, 0, 1) * 255).astype(np.uint8)):
                raise OSError(f"could not write {path}")
        except OSError as e:
            log.warning("could not save learned item name %s: %s", name, e)
            return
        log.info("learned item name template: %s (%dx%d)", name, strip.shape[1], strip.shape[0])
        self.learned.append(name)
        items.reload_templates()
