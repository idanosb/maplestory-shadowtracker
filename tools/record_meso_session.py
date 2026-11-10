"""Record the game client area for meso-reader development (capture only).

Writes recordings/NAME/ with:
  pickups_0001.png ...  1 Hz crop of the client area's right 40% x bottom 45%
  full_0001.png ...     full client frame at start, every 10 s, and at end
  index.csv             frame,time,kind
Beeps once at start and twice at end.
"""
import argparse
import csv
import sys
import time
import winsound
from datetime import datetime

import _common
import cv2

from expstrip.capture import grab
from expstrip.win.window import find_game_window

RECORDINGS = _common.ROOT / "recordings"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--title", default="MapleStory")
    ap.add_argument("--delay", type=float, default=5.0)
    a = ap.parse_args()
    out = RECORDINGS / a.name
    out.mkdir(parents=True, exist_ok=True)
    seconds = max(1, round(a.minutes * 60))
    print(f"Click into the game now — recording starts in {a.delay:.0f}s ({seconds}s total).")
    time.sleep(a.delay)

    npick = nfull = skipped = 0
    with open(out / "index.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "time", "kind"])

        def save(frame, kind: str, n: int) -> None:
            fname = f"{kind}_{n:04d}.png"
            cv2.imwrite(str(out / fname), frame)
            w.writerow([fname, datetime.now().isoformat(timespec="seconds"), kind])
            f.flush()

        winsound.Beep(880, 120)
        t0 = time.monotonic()
        for i in range(seconds):
            win = find_game_window(a.title)
            frame = grab(win) if win is not None else None
            if frame is None:
                skipped += 1
            else:
                h, wd = frame.shape[:2]
                npick += 1
                save(frame[int(h * 0.55):, int(wd * 0.60):], "pickups", npick)
                if i % 10 == 0 or i == seconds - 1:
                    nfull += 1
                    save(frame, "full", nfull)
            time.sleep(max(0.0, t0 + i + 1 - time.monotonic()))
        winsound.Beep(880, 120)
        winsound.Beep(880, 120)
    print(f"done: {npick} pickups, {nfull} full, {skipped}s skipped -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
