"""Generate assets/tray.ico: the orange circle with "XP" (same look as the tray icon).

Run on the real Qt platform (needs fonts):  .venv/Scripts/python tools/make_icon.py
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from expstrip.ui import theme  # noqa: E402

SIZES = (16, 32, 48, 64, 128, 256)


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ORANGE))
    m = size / 32
    p.drawEllipse(int(m), int(m), int(30 * m), int(30 * m))
    p.setPen(QColor("#ffffff"))
    p.setFont(theme.font(max(6, round(14 * m)), QFont.Weight.ExtraBold))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "XP")
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def write_ico(path: Path) -> None:
    pngs = [(s, png_bytes(draw(s))) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    body = b""
    for s, data in pngs:
        b = 0 if s >= 256 else s
        out += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        body += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out + body)


if __name__ == "__main__":
    app = QApplication([])
    target = Path(__file__).resolve().parent.parent / "assets" / "tray.ico"
    write_ico(target)
    print("wrote", target, target.stat().st_size, "bytes")
