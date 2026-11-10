"""Probe: how well does Windows OCR read the item names of pickup lines?

    .venv\\Scripts\\python tools\\ocr_names_probe.py recordings\\loot_a [recordings\\tail_a ...]

For each item line whose suffix and quantity read, OCRs the name area left of the "x"
and prints the template name (if any) next to the OCR text.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

import _common  # noqa: F401
import cv2
import numpy as np
from replay_pickups import region_in_crop

from expstrip.vision import items, pickups
from expstrip.vision.glyphs import to_native
from expstrip.vision.ocr import ocr_lines
from expstrip.vision.pickups import LINE_H, ink, region_changed


def name_crop(im: np.ndarray, x_left: int, y: int) -> np.ndarray:
    """Name area of an item line as black text on white, BGR, for OCR."""
    end = x_left - items.NAME_GAP + 2
    band = im[max(0, y - 1):y + LINE_H + 1, max(0, end - 260):end]
    band = np.clip(band * (255.0 / max(1.0, float(band.max()))), 0, 255).astype(np.uint8)
    inv = 255 - band
    inv = cv2.copyMakeBorder(inv, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(inv, cv2.COLOR_GRAY2BGR)


def reads(region: np.ndarray, mode: str):
    tpl = items._templates()
    digits = [(ch, g) for ch, g in pickups._templates()[1] if ch.isdigit()] + tpl["digits"]
    im = ink(to_native(region, 1.0))
    for y, sx, tab in items._suffix_hits(im, tpl["suffix"]):
        q = items._read_qty(im, sx, y, digits, tpl["x"])
        if q is None:
            continue
        _, x_left = q
        vis = items._read_name(im, x_left, y, tpl["names"])
        if mode == "ink":
            img = name_crop(im, x_left, y)
        else:  # raw colour crop, upscaled by ocr_lines itself
            end = x_left - items.NAME_GAP + 2
            img = to_native(region, 1.0)[max(0, y - 3):y + LINE_H + 3, max(0, end - 260):end]
        text = " ".join(ocr_lines(img))
        yield vis, text


def main(argv: list[str]) -> int:
    mode = "raw" if "--raw" in argv else "ink"
    stats: Counter = Counter()
    for rec in [Path(a) for a in argv if not a.startswith("--")]:
        with open(rec / "index.csv", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["kind"] == "pickups"]
        prev = None
        for row in rows:
            crop = cv2.imread(str(rec / row["frame"]))
            if crop is None or not crop.any():
                continue
            region = region_in_crop(crop)
            if not region_changed(prev, region):
                continue
            prev = region
            for vis, text in reads(region, mode):
                stats[(vis, text)] += 1
    for (vis, text), n in sorted(stats.items(), key=lambda kv: (str(kv[0][0]), -kv[1])):
        print(f"{n:4d}  tpl={vis!s:22s} ocr={text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
