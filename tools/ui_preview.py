"""Show the strip and panel with fake data and print affinity / lock results.

Run: .venv\\Scripts\\python tools\\ui_preview.py [seconds]   (default 8 s, then exits)
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from expstrip.config import Settings
from expstrip.history import SpotSummary
from expstrip.reading import MapName
from expstrip.tracker import Stats
from expstrip.ui.spots_panel import SpotsPanel
from expstrip.ui.strip import StripWindow
from expstrip.worker import Snapshot, StatusKind


def main() -> None:
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    app = QApplication([])
    strip = StripWindow(Settings(), Path(tempfile.gettempdir()) / "expstrip_preview.json")
    panel = SpotsPanel(strip)
    now = datetime.now()
    panel.apply([
        SpotSummary("Ant Tunnel I", "Ant Tunnel I", 71200, 2520, now, True, 41000),
        SpotSummary("Wild Boar Land", "Wild Boar Land", 58900, 4200, now - timedelta(days=1), False, -2300),
        SpotSummary("Kerning Subway B2", "Kerning Subway B2", 48600, 1500, now, False),
        SpotSummary("Slime Tree", "Slime Tree", 22300, 900, now - timedelta(days=2), False),
    ], MapName("", "Ant Tunnel I"))
    here = MapName("", "Ant Tunnel I")
    positive = Stats(44, 30.9, here, 71200, 15300, 6100, 49000, False, 12400, 15600, 3200, 41000)
    negative = Stats(44, 30.9, here, 71200, 15300, 6100, 49000, False, -800, 0, 800, None)
    strip.apply(Snapshot(StatusKind.OK, positive, []))
    strip.show()
    strip._toggle_panel()
    app.processEvents()
    if out:
        out.mkdir(parents=True, exist_ok=True)
        strip.grab().save(str(out / "strip_positive.png"))
        panel.grab().save(str(out / "panel.png"))
        strip.apply(Snapshot(StatusKind.OK, negative, []))
        strip.reset_button.click()  # shows the confirm state
        app.processEvents()
        strip.grab().save(str(out / "strip_negative.png"))
        strip.apply(Snapshot(StatusKind.OK, positive, []))
    print("strip affinity:", strip.affinity_ok)
    print("panel visible:", panel.isVisible())
    strip.set_locked(True)
    print("lock on  ok:", strip.click_through_ok)
    strip.set_locked(False)
    print("lock off ok:", strip.click_through_ok)
    QTimer.singleShot(secs * 1000, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
