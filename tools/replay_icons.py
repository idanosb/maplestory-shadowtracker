"""Replay a recording through the quickslot icon learner (expstrip.icon_learner).

    .venv\\Scripts\\python tools\\replay_icons.py recordings\\loot_a [Name ...]

Pickup crops give the item lines (counted once, as the app does); full frames give the
quickslot scans. Potions named on the command line (default: Orange Potion, Blue Potion)
are treated as price-list potions without an icon. Learned icons go to a temp folder,
whose path is printed; the key each one came from is printed too.
"""
from __future__ import annotations

import csv
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401
import cv2
from replay_pickups import region_in_crop

from expstrip.icon_learner import IconLearner
from expstrip.vision import quickslots
from expstrip.vision.items import ItemLine, read_item_lines
from expstrip.vision.pickups import PickupCounter, region_changed


def main(argv: list[str]) -> int:
    rec = Path(argv[0])
    potions = argv[1:] or ["Orange Potion", "Blue Potion"]
    out = Path(tempfile.mkdtemp(prefix="learned_icons_"))
    learner = IconLearner(out)
    with open(rec / "index.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    t0 = datetime.fromisoformat(rows[0]["time"])
    counter, prev = PickupCounter(), None
    for row in rows:
        now = (datetime.fromisoformat(row["time"]) - t0).total_seconds()
        img = cv2.imread(str(rec / row["frame"]))
        if img is None or not img.any():
            continue
        if row["kind"] == "full":
            got = learner.on_scan(img, 1.0, now)
            counts = quickslots.read_key_counts(img, 1.0)
            print(f"{now:6.0f}s scan {row['frame']} counts {counts}"
                  + (f"  pending {dict((k, v['qty']) for k, v in learner._pending.items())}" if learner._pending else ""))
            for name in got:
                print(f"        LEARNED {name} -> {out / (name + '.png')}")
            continue
        region = region_in_crop(img)
        if not region_changed(prev, region):
            continue
        prev = region
        lines = read_item_lines(region, 1.0, potions, with_level=True)
        new = [k for k in counter.feed(lines) if isinstance(k, ItemLine)]
        if new:
            print(f"{now:6.0f}s pickup " + ", ".join(f"{k.name} x{k.qty}" for k in new))
            learner.on_items(new, potions, now)
    print("learned:", learner.learned or "nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
