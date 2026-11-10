"""Checks that an overlay is visible over the game yet excluded from capture."""
import argparse
import sys

import _common
import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel

from expstrip.capture import grab, is_blank
from expstrip.win.affinity import exclude_from_capture
from expstrip.win.window import find_game_window, list_visible_titles


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="MapleStory")
    a = ap.parse_args()
    win = find_game_window(a.title)
    if win is None:
        print(f"Game window '{a.title}' not found (or minimized). Visible windows:")
        for t in sorted(set(list_visible_titles())):
            print("  ", t)
        return 1

    app = QApplication(sys.argv[:1])
    lab = QLabel("ExpStrip visibility check")
    lab.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    lab.setAttribute(Qt.WA_TranslucentBackground)
    lab.setAlignment(Qt.AlignCenter)
    lab.setStyleSheet("background:#fbf7ef; color:#7d7468; border-radius:30px; font-size:18px;")
    lab.resize(560, 60)
    lab.move(win.left + (win.width - 560) // 2, win.top + 10)
    lab.show()
    print("exclude_from_capture:", exclude_from_capture(int(lab.winId())))

    def snap():
        frame = grab(win)
        _common.FIXTURES.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(_common.FIXTURES / "visibility.png"), frame)
        print("capture:", "BLANK" if is_blank(frame) else "OK")
        print(f"client size: {win.width}x{win.height}")

    QTimer.singleShot(5000, snap)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
