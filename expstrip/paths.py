import sys
from pathlib import Path


def app_dir() -> Path:
    """Exe dir when frozen, otherwise the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def templates_dir() -> Path:
    """Bundled template PNGs; under sys._MEIPASS when frozen by PyInstaller."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "expstrip" / "templates"
    return Path(__file__).resolve().parent / "templates"
