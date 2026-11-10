"""Colors and fonts (dark, slim look). Rubik is not bundled, so Segoe UI (Windows default) is used."""
from PySide6.QtGui import QFont

FONT_FAMILY = "Segoe UI"

BG = "#1e1f24"          # strip / panel background
BORDER = "#34363d"      # 1px outline, row rules, empty progress track
HOVER = "#2b2d34"       # button hover fill
ORANGE = "#ff7a3d"      # progress fill, accents, tray icon
PEACH = "#ffb48a"       # level, best-spot rate
WHITE = "#ffffff"       # the headline number
TEXT = "#c9cbd2"        # regular values
MUTED = "#9a9ca5"       # captions, icons, units
TRACK = BORDER
BAR = PEACH
POSITIVE = "#69db7c"
NEGATIVE = "#ff8787"


def font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f
