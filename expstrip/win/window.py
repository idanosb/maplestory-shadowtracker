from dataclasses import dataclass

import win32gui


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    left: int
    top: int
    width: int
    height: int


def list_visible_titles() -> list[str]:
    titles: list[str] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t:
                titles.append(t)

    win32gui.EnumWindows(cb, None)
    return titles


def find_game_window(title_substr: str) -> GameWindow | None:
    needle = title_substr.lower()
    found: list[int] = []

    def cb(hwnd, _):
        if needle in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    for hwnd in found:
        if win32gui.IsIconic(hwnd):
            continue
        _, _, w, h = win32gui.GetClientRect(hwnd)
        if w <= 0 or h <= 0:
            continue
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        return GameWindow(hwnd, left, top, w, h)
    return None
