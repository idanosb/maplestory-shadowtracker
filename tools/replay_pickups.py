"""Replay a meso recording's pickup crops through the pickup reader and sum pickups per window.

    .venv\\Scripts\\python tools\\replay_pickups.py recordings\\meso_b [-v]

The recording's pickup crops are the client's right 40% x bottom 45% (see
tools/record_meso_session.py). For every window between consecutive full frames
(T_prev < t <= T_next, from index.csv) it prints the sum of counted pickups.
With -v it also prints each frame's new pickups.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import _common  # noqa: F401  (puts the repo root on sys.path)
import cv2
import numpy as np

from expstrip.vision.pickups import PickupCounter, pickup_rect, read_pickup_lines, region_changed

# Crop origin of pickups_*.png inside the 2560x1440 client (right 40%, bottom 45%).
CLIENT_W, CLIENT_H = 2560, 1440
CROP_X, CROP_Y = 1536, 792


def region_in_crop(crop: np.ndarray):
    """pickup_rect of the full client, shifted into the recording's crop."""
    r = pickup_rect(np.empty((CLIENT_H, CLIENT_W, 1), np.uint8), 1.0)
    return crop[r.y - CROP_Y:r.y - CROP_Y + r.h, r.x - CROP_X:r.x - CROP_X + r.w]


def main(argv: list[str]) -> int:
    rec = Path(argv[0])
    verbose = "-v" in argv
    with open(rec / "index.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    counter, prev = PickupCounter(), None
    window, windows, last_full = [], [], None
    ocr_calls, ocr_time = 0, 0.0
    for row in rows:
        if row["kind"] == "pickups":
            crop = cv2.imread(str(rec / row["frame"]))
            if crop is None or not crop.any():  # missing or black capture
                continue
            region = region_in_crop(crop)
            if region_changed(prev, region):
                t = time.perf_counter()
                new = counter.feed(read_pickup_lines(region, 1.0))
                ocr_time += time.perf_counter() - t
                ocr_calls += 1
                prev = region
                window += new
                if verbose and new:
                    print(f"  {row['frame']} {row['time'][11:]} +{new}")
        else:
            if last_full is not None:
                windows.append((last_full, row["frame"], window))
            last_full, window = row["frame"], []
    total = 0
    for a, b, w in windows:
        total += sum(w)
        print(f"{a} -> {b}: {sum(w):6d}  ({len(w)} pickups)")
    print(f"total counted {total}; OCR calls {ocr_calls}; avg read+feed {ocr_time / max(1, ocr_calls) * 1000:.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
