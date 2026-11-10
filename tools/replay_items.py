"""Replay a loot recording's pickup crops through the item-line reader and count pickups.

    .venv\\Scripts\\python tools\\replay_items.py recordings\\loot_a [-v] [--lines] [--with-meso] [--no-level]

The recording's pickup crops are the client's right 40% x bottom 45% (see
tools/record_meso_session.py, tools/replay_pickups.py). The item lines of every changed
crop, with their brightness level, are fed to a PickupCounter (keys: ItemLine), so each
line is counted once. It prints the counted quantity per name and the share of the truth
captured. -v prints each frame's new item lines; --lines every frame's item lines read;
--with-meso feeds the meso lines to the same counter too; --no-level leaves the levels out.
--learn NAME[,NAME] hides those bundled name templates and reads them with the OCR fallback
(item_names.NameLearner, learning into a temp folder), to check names are picked up alone.
"""
from __future__ import annotations

import csv
import sys
import time
from collections import Counter
from pathlib import Path

import _common  # noqa: F401  (puts the repo root on sys.path)
import cv2
from replay_pickups import region_in_crop

import tempfile

from expstrip.vision import items
from expstrip.vision.item_names import NameLearner
from expstrip.vision.items import ItemLine, read_item_lines
from expstrip.vision.pickups import PickupCounter, read_pickup_lines, region_changed

NAMES = ["Tablecloth", "Stirge Wing", "Curse Eye Tail", "Meat", "Lemon"]
TRUTH = {"loot_a": {"Tablecloth": 40, "Stirge Wing": 15}}  # Etc tab, start 0


def main(argv: list[str]) -> int:
    rec = Path(argv[0])
    verbose, show_lines = "-v" in argv, "--lines" in argv
    with open(rec / "index.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["kind"] == "pickups"]
    fallback = None
    if "--learn" in argv:
        hide = set(argv[argv.index("--learn") + 1].split(","))
        tpl = items._templates()
        tpl["names"] = [(v, t) for v, t in tpl["names"] if v not in hide]
        learned = Path(tempfile.mkdtemp(prefix="learned_items_"))
        fallback = NameLearner(None)
        orig_offer = fallback._offer

        def offer(name, strip):  # learn into the in-memory set (keeps the hidden ones hidden)
            fallback._seen[name] = fallback._seen.get(name, 0) + 1
            if fallback._seen[name] == 2 and not any(v == name for v, _ in tpl["names"]):
                tpl["names"].append((name, (strip * 255).astype("float32")))
                cv2.imwrite(str(learned / f"{name}.png"), (strip * 255).astype("uint8"))
                print(f"  learned {name} at {row['frame']} -> {learned}")
        fallback._offer = offer
        del orig_offer
    counter, prev = PickupCounter(), None
    totals: Counter[str] = Counter()
    calls, spent = 0, 0.0
    for row in rows:
        crop = cv2.imread(str(rec / row["frame"]))
        if crop is None or not crop.any():  # missing or black capture
            continue
        region = region_in_crop(crop)
        if not region_changed(prev, region):
            continue
        t = time.perf_counter()
        item_lines = read_item_lines(region, 1.0, NAMES, with_level="--no-level" not in argv,
                                     fallback=fallback)
        lines = item_lines + (read_pickup_lines(region, 1.0) if "--with-meso" in argv else [])
        new = counter.feed(lines)
        spent += time.perf_counter() - t
        calls += 1
        prev = region
        new_items = [k for k in new if isinstance(k, ItemLine)]
        for k in new_items:
            totals[k.name] += k.qty
        if show_lines and item_lines:
            print(f"  {row['frame']} " + ", ".join(f"{t[0].name} x{t[0].qty} ({t[0].tab}) @{t[1]:.0f}" + (f" b{t[2]:.0f}" if len(t) > 2 else "") for t in item_lines))
        if verbose and new_items:
            print(f"  {row['frame']} {row['time'][11:]} new " + ", ".join(f"{k.name} x{k.qty}" for k in new_items))
    truth = TRUTH.get(rec.name, {})
    for name in sorted(set(totals) | set(truth)):
        print(f"{name:12s} counted {totals[name]:4d}" + (f"  truth {truth[name]:4d}" if name in truth else ""))
    got = sum(min(totals[n], truth[n]) for n in truth) if truth else 0
    over = sum(max(0, totals[n] - truth.get(n, 0)) for n in totals)
    want = sum(truth.values())
    if truth:
        print(f"captured {got}/{want} ({got / want:.0%}); over-counted {over}")
    print(f"reads {calls}; avg read+feed {spent / max(1, calls) * 1000:.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
