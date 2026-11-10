import ctypes

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11


def set_capture_excluded(hwnd: int, excluded: bool) -> bool:
    """Hide one of our own overlay windows from screen capture (excluded=True), or show it in
    screen shares / recordings again (excluded=False)."""
    mode = WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE
    return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(hwnd), mode))


def exclude_from_capture(hwnd: int) -> bool:
    """Hide one of our own overlay windows from screen capture."""
    return set_capture_excluded(hwnd, True)
