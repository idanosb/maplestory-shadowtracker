import mss
import numpy as np

from expstrip.win.window import GameWindow


def grab(win: GameWindow) -> np.ndarray:
    """Desktop-region capture of the client area; BGR uint8 (h, w, 3)."""
    region = {"left": win.left, "top": win.top, "width": win.width, "height": win.height}
    with mss.mss() as sct:
        shot = sct.grab(region)
    return np.array(shot)[:, :, :3].copy()  # BGRA -> BGR


def is_blank(frame: np.ndarray) -> bool:
    return float(frame.mean()) < 3 and float(frame.std()) < 2
