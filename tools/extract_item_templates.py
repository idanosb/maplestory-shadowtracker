"""Extract the item-line templates (suffixes, "x", names) from a recording.

    .venv\\Scripts\\python tools\\extract_item_templates.py recordings\\loot_a "43:171:Stirge Wing:1:Etc" ...

Each sample is FRAME:Y:NAME:QTY:TAB: a pickups_FRAME.png crop of the recording, the center
row Y of an item line inside the pickup region (as tools/replay_items.py crops it), and the
visible name (with "..." if the game cut it), quantity and tab, labeled by eye. Pick fresh,
uncovered lines. Measured on loot_a, the line ends at its last ink column; the suffix
template is the SUFFIX_W[tab] columns ending 1 px right of it. The qty digits are found
with the meso digit templates left of the suffix; the "x" and the name are cut from the ink
spans left of them (name: spans with gaps <= 9 px, i.e. word spaces). A template is stored
as a new variant (`Tablecloth_2.png`) when it matches every stored variant below --variant.
A template for a new name is learned the same way from a short recording of picking it up.
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
from expstrip.vision import items
from expstrip.vision.glyphs import score
from expstrip.vision.pickups import LINE_H, _templates as pickup_templates, ink

OUT = templates_dir() / "items"
_WORD_GAP = 9


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


def _save(img: np.ndarray, stem: str, variant: float, single: bool = False) -> str | None:
    known = sorted(OUT.glob(f"{stem}.png")) + sorted(OUT.glob(f"{stem}_*.png"))
    if single and known:
        return None
    tpls = [cv2.imread(str(p), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255 for p in known]
    if tpls and max(score(img, t) for t in tpls) >= variant:
        return None
    name = stem if not known else f"{stem}_{len(known) + 1}"
    cv2.imwrite(str(OUT / f"{name}.png"), (img * 255).astype(np.uint8))
    return name


def _find_digit(strip: np.ndarray, ch: str, right: tuple[int, int]) -> tuple[float, int]:
    best = (-1.0, 0)
    own = [(c[3], cv2.imread(str(p), cv2.IMREAD_GRAYSCALE).astype(np.float32))
           for p in OUT.glob("qty*.png") for c in [p.stem]]
    for c, tpl in pickup_templates()[1] + own:
        if c == ch:
            best = max(best, items._best_in(strip, 1, right, tpl))
    return best


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
    for sample in a.samples:
        frame, y, name, qty, tab = sample.split(":")
        crop = cv2.imread(str(Path(a.recording) / f"pickups_{int(frame):04d}.png"))
        im = ink(region_in_crop(crop))
        y0 = int(y) - LINE_H // 2
        strip = im[y0:y0 + LINE_H]
        strip = np.clip(strip / max(1.0, np.percentile(strip, 99.5)), 0, 1)
        spans = _spans((strip > 0.35).any(axis=0))
        if not spans:
            print(f"{sample}: no ink", file=sys.stderr)
            continue
        end = spans[-1][1]
        sx = end + 1 - items.SUFFIX_W[tab]
        if _save(strip[:, sx:end + 1], f"suffix_{tab}", a.variant, single=True):
            print(f"{sample}: saved suffix_{tab}")
        # qty digits right to left, padded by one row so _best_in may shift by +/- 1
        padded = np.pad(strip, ((1, 1), (0, 0)))
        left, rng = sx, items._QTY_RIGHT
        for ch in reversed(qty):
            sc, left = _find_digit(padded, ch, (left + rng[0], left + rng[1]))
            if sc < 0.6:
                print(f"{sample}: digit {ch} not found ({sc:.2f})", file=sys.stderr)
                break
            if _save(strip[:, left:left + 12], f"qty{ch}", a.variant):
                print(f"{sample}: saved qty{ch}")
            rng = items._DIGIT_RIGHT
        else:
            xspan = [s for s in spans if left - 14 <= s[0] and s[1] <= left + items._X_RIGHT[1]
                     and s[1] <= left + 6]
            if not xspan:
                print(f"{sample}: no 'x' ink left of column {left}", file=sys.stderr)
                continue
            xl, xr = xspan[0][0], xspan[-1][1]
            if _save(strip[:, xl - 1:xr + 1], "x", a.variant):
                print(f"{sample}: saved x")
            word = [s for s in spans if s[1] <= xl - 2]
            nl = word[-1][0] if word else xl
            for s in reversed(word[:-1]):
                if nl - s[1] > _WORD_GAP:
                    break
                nl = s[0]
            nr = xl - 1 - items.NAME_GAP
            saved = _save(strip[:, nl - 1:nr], name, a.variant)
            print(f"{sample}: name cols {nl - 1}..{nr} " + (f"saved {saved}" if saved else "(known)"))
    items.reload_templates()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
