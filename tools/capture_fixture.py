"""Grab the game client area (no overlay) to tests/fixtures/NAME.png at native size.

Waits --delay seconds first (so you can click back into the game and the Windows
taskbar hides). With --count N it takes N captures --every S seconds, saved as
NAME_01.png, NAME_02.png, ... A short beep sounds right before each capture.
"""
import argparse
import sys
import time
import winsound

import _common
import cv2

from expstrip.capture import grab
from expstrip.win.window import find_game_window


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--title", default="MapleStory")
    ap.add_argument("--delay", type=float, default=5.0)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--every", type=float, default=15.0)
    a = ap.parse_args()
    _common.FIXTURES.mkdir(parents=True, exist_ok=True)
    print(f"Click into the game now — first capture in {a.delay:.0f}s.")
    time.sleep(a.delay)
    for i in range(a.count):
        if i:
            time.sleep(a.every)
        win = find_game_window(a.title)
        if win is None:
            print(f"Game window '{a.title}' not found (or minimized).")
            return 1
        winsound.Beep(880, 120)
        name = a.name if a.count == 1 else f"{a.name}_{i + 1:02d}"
        out = _common.FIXTURES / f"{name}.png"
        cv2.imwrite(str(out), grab(win))
        print(f"saved {out} ({win.width}x{win.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
