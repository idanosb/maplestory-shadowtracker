"""Win32 helpers for our own overlay windows (click-through)."""
import ctypes
from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x2

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
_user32.SetLayeredWindowAttributes.restype = wintypes.BOOL


def set_click_through(hwnd: int, on: bool) -> bool:
    """Make our own window ignore mouse input (on) or accept it again (off)."""
    h = wintypes.HWND(int(hwnd))
    style = _user32.GetWindowLongW(h, GWL_EXSTYLE) | WS_EX_LAYERED
    style = (style | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
    _user32.SetWindowLongW(h, GWL_EXSTYLE, style)
    if not _user32.SetLayeredWindowAttributes(h, 0, 255, LWA_ALPHA):
        return False
    return bool(_user32.GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TRANSPARENT) == on


WS_EX_NOACTIVATE = 0x08000000


def get_ex_style(hwnd: int) -> int:
    return _user32.GetWindowLongW(wintypes.HWND(int(hwnd)), GWL_EXSTYLE) & 0xFFFFFFFF


def set_no_activate(hwnd: int) -> bool:
    """Clicks on our overlay must never take keyboard focus from the game."""
    h = wintypes.HWND(int(hwnd))
    _user32.SetWindowLongW(h, GWL_EXSTYLE, _user32.GetWindowLongW(h, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
    return bool(get_ex_style(hwnd) & WS_EX_NOACTIVATE)
