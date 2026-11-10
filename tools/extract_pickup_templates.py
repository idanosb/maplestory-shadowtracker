"""Extract the pickup-line templates (prefix "...gained mesos (+", digits, ")") from recordings.

    .venv\\Scripts\\python tools\\extract_pickup_templates.py recordings\\meso_b --reset 9:224:79 23:224:42 ...

Each sample is FRAME:Y:AMOUNT: a pickups_FRAME.png crop of the recording, the center row Y
of a meso line inside the pickup region (as tools/replay_pickups.py crops it), and the
amount shown, labeled by eye. The font has fixed cells, measured on meso_b: the line ends
with ")" (6 px wide, starting 6 px before the line's last ink column), digits are 10 px wide
at a 13 px pitch with the last one 11 px left of ")", and "+" sits one pitch left of the
first digit. Cells are cut at those positions (with a 1 px margin), so touching glyphs do
not break the extraction. The first sample also gives the prefix: the PREFIX_W columns
ending at the "+". A glyph is stored as a new variant (`7_2.png`) when it matches every
stored variant of its character below --variant.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from _common import ROOT  # noqa: F401  (also puts the repo root on sys.path)
from replay_pickups import region_in_crop

from expstrip.paths import templates_dir
from expstrip.vision.glyphs import score
from expstrip.vision.pickups import LINE_H, PREFIX_W, ink

OUT = templates_dir() / "pickup"
NAMES = {")": "rpar", ",": "comma"}


def _spans(cols: np.ndarray) -> list[tuple[int, int]]:
    out, c = [], 0
    while c < len(cols):
        if cols[c]:
            a = c
            while c < len(cols) and cols[c]:
                c += 1
            out.append((a, c))
        else:
            c += 1
    return out


def _save(img: np.ndarray, ch: str, variant: float) -> str | None:
    stem = NAMES.get(ch, ch)
    known = sorted(OUT.glob(f"{stem}.png")) + sorted(OUT.glob(f"{stem}_*.png"))
    tpls = [cv2.imread(str(p), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255 for p in known]
    if tpls and max(score(img, t) for t in tpls) >= variant:
        return None
    name = stem if not known else f"{stem}_{len(known) + 1}"
    cv2.imwrite(str(OUT / f"{name}.png"), (img * 255).astype(np.uint8))
    return name


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("samples", nargs="+")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--variant", type=float, default=0.93)
    a = ap.parse_args(argv)
    if a.reset and OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    for k, sample in enumerate(a.samples):
        frame, y, amount = sample.split(":")
        crop = cv2.imread(str(Path(a.recording) / f"pickups_{int(frame):04d}.png"))
        im = ink(region_in_crop(crop))
        y0 = int(y) - LINE_H // 2
        strip = im[y0:y0 + LINE_H]
        strip = np.clip(strip / max(1.0, np.percentile(strip, 99.5)), 0, 1)
        spans = _spans((strip > 0.35).any(axis=0))
        if not spans:
            print(f"{sample}: no ink", file=sys.stderr)
            continue
        r = spans[-1][1] - 6                       # left column of ")"
        n = len(amount)
        digit_x = [r - 11 - 13 * (n - 1 - k) for k in range(n)]
        plus_end = digit_x[0] - 13 + 9
        if k == 0 or not (OUT / "prefix.png").exists():
            cv2.imwrite(str(OUT / "prefix.png"), (strip[:, plus_end - PREFIX_W:plus_end + 1] * 255).astype(np.uint8))
            print("saved prefix")
        cells = [(x - 1, x + 11, ch) for x, ch in zip(digit_x, amount)] + [(r - 1, r + 7, ")")]
        for x0, x1, ch in cells:
            name = _save(strip[:, x0:x1], ch, a.variant)
            if name:
                print(f"{sample}: saved {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
