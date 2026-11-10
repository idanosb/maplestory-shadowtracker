import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint

from expstrip.config import Settings
from expstrip.history import SpotSummary
from expstrip.reading import MapName
from expstrip.tracker import Stats
from expstrip.ui.spots_panel import SpotsPanel
from expstrip.ui.strip import StripWindow
from expstrip.worker import Snapshot, StatusKind

EMPTY_STATS = Stats(None, None, None, None, None, 0, 0, False)
ROOT = Path(__file__).resolve().parent.parent


def test_strip_shows_values(qapp, tmp_path):
    w = StripWindow(Settings(), tmp_path / "s.json")
    w.apply(Snapshot(StatusKind.OK, Stats(44, 26.78, MapName("", "A"), 48600, 15660, 4200, 31300, False), []))
    assert w.value_texts() == ["48.6K", "4h 21m", "+4.2K", "+31.3K"] and w.level_text() == "44"


def test_strip_status_text(qapp, tmp_path):
    w = StripWindow(Settings(), tmp_path / "s.json")
    w.apply(Snapshot(StatusKind.NOT_VISIBLE, EMPTY_STATS, []))
    assert w.status_text() == "Game not visible — use windowed mode"


def test_strip_paused_keeps_level(qapp, tmp_path):
    w = StripWindow(Settings(), tmp_path / "s.json")
    w.apply(Snapshot(StatusKind.OK, Stats(44, 26.78, None, 48600, 15660, 4200, 31300, False), []))
    w.apply(Snapshot(StatusKind.PAUSED, EMPTY_STATS, []))
    assert w.status_text() == "Paused" and w.level_text() == "44"


def test_strip_drag_saves_position(qapp, tmp_path):
    s = Settings()
    w = StripWindow(s, tmp_path / "s.json")
    w.move(120, 80)
    w._save_position()
    assert (s.strip_x, s.strip_y) == (120, 80) and (tmp_path / "s.json").exists()


def test_panel_rows(qapp, tmp_path):
    p = SpotsPanel(StripWindow(Settings(), tmp_path / "s.json"))
    p.apply([SpotSummary("A", "A", 71200, 2520, datetime.now(), True), SpotSummary("B", "B", 48600, 1500, datetime.now(), False)], MapName("", "B"))
    assert p.row_texts()[0][0].startswith("🏆 A") and p.row_texts()[1][0].endswith(" · now")


def test_panel_empty(qapp, tmp_path):
    p = SpotsPanel(StripWindow(Settings(), tmp_path / "s.json"))
    p.apply([], None)
    assert p.row_texts() == [] and p.empty_text() == "No spots yet — train for a minute somewhere."


@pytest.mark.skipif(sys.platform != "win32", reason="Windows display affinity")
def test_affinity_and_click_through_on_real_window(tmp_path):
    code = (
        "import sys;from PySide6.QtWidgets import QApplication;"
        "from expstrip.config import Settings;from expstrip.ui.strip import StripWindow;from expstrip.win.overlay import get_ex_style;"
        "a=QApplication([]);w=StripWindow(Settings(),sys.argv[1]);w.show();a.processEvents();"
        "print('AFF',w.affinity_ok, w.no_activate_ok and bool(get_ex_style(int(w.winId())) & 0x08000000), w.set_locked(True) or w.click_through_ok, w.set_locked(False) or w.click_through_ok)"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "QT_QPA_PLATFORM"}
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path / "s.json")],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert "AFF True True True True" in r.stdout, r.stdout + r.stderr


def test_panel_escapes_map_names(qapp, tmp_path):
    p = SpotsPanel(StripWindow(Settings(), tmp_path / "s.json"))
    p.apply([SpotSummary("Line 1 <Area 2>", "Line 1 <Area 2>", 1000, 600, datetime.now(), True)],
            MapName("", "Line 1 <Area 2>"))
    assert p.display_names() == ["🏆 Line 1 <Area 2> · now"]


def test_level_shown_on_fresh_window_when_not_ok(qapp, tmp_path):
    w = StripWindow(Settings(), tmp_path / "s.json")
    w.apply(Snapshot(StatusKind.NOT_VISIBLE, Stats(44, None, None, None, None, 0, 0, False), []))
    assert w.level_text() == "44"


def test_offscreen_saved_position_is_clamped(qapp, tmp_path):
    default = StripWindow(Settings(), tmp_path / "a.json").pos()
    far = StripWindow(Settings(strip_x=99999, strip_y=99999), tmp_path / "b.json")
    assert far.pos() == default
    ok = StripWindow(Settings(strip_x=10, strip_y=20), tmp_path / "c.json")
    assert (ok.x(), ok.y()) == (10, 20)


def test_release_without_move_does_not_save(qapp, tmp_path):
    s = Settings()
    w = StripWindow(s, tmp_path / "s.json")
    w._press_pos = w.pos()
    w._drag = QPoint(1, 1)
    w.mouseReleaseEvent(None)
    assert s.strip_x is None


# -- meso line, reset button, Mesos/hour column ---------------------------
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from expstrip.ui import theme  # noqa: E402


def _meso_strip(tmp_path, **kw):
    w = StripWindow(Settings(), tmp_path / "s.json")
    w.apply(Snapshot(StatusKind.OK, Stats(44, 26.78, None, 48600, 15660, 4200, 31300, False, **kw), []))
    return w


def test_meso_line_text_and_color(qapp, tmp_path):
    w = _meso_strip(tmp_path, mesos_net=12_400, mesos_picked=15_600, potion_cost=3_200, mesos_per_hour=41_000)
    assert w.meso_text() == "Mesos +12.4K · 41K/h"
    assert theme.POSITIVE in w.meso_net_style()


def test_meso_rate_dash_before_60s(qapp, tmp_path):
    w = _meso_strip(tmp_path, mesos_net=-800, mesos_picked=0, potion_cost=800, mesos_per_hour=None)
    assert w.meso_text().endswith("—/h") and theme.NEGATIVE in w.meso_net_style()


def test_meso_zero_is_muted(qapp, tmp_path):
    w = _meso_strip(tmp_path)
    assert w.meso_text() == "Mesos +0 · —/h"
    assert theme.MUTED in w.meso_net_style()


def test_meso_line_hidden_when_not_ok(qapp, tmp_path):
    w = _meso_strip(tmp_path)
    w.show()
    w.apply(Snapshot(StatusKind.PAUSED, EMPTY_STATS, []))
    assert not w._meso.isVisible()
    w.close()


def _click(w):
    QTest.mouseClick(w.reset_button, Qt.MouseButton.LeftButton)


def test_single_click_does_not_reset(qapp, tmp_path):
    w = _meso_strip(tmp_path)
    fired = []
    w.reset_requested.connect(lambda: fired.append(1))
    _click(w)
    assert fired == [] and w.reset_button.kind == "reset"
    assert w.reset_button.toolTip() == "Click again to reset"


def test_second_click_within_3s_resets(qapp, tmp_path):
    w = _meso_strip(tmp_path)
    fired = []
    w.reset_requested.connect(lambda: fired.append(1))
    _click(w)
    _click(w)
    assert fired == [1] and w.reset_button.toolTip() == "Reset session"


def test_confirm_state_expires(qapp, tmp_path):
    w = _meso_strip(tmp_path)
    fired = []
    w.reset_requested.connect(lambda: fired.append(1))
    _click(w)
    QTest.qWait(3100)
    _click(w)
    assert fired == []


def test_mask_matches_strip_height(qapp, tmp_path):
    w = _meso_strip(tmp_path)
    assert w.mask().boundingRect().height() == w.height()


def test_panel_meso_column(qapp, tmp_path):
    p = SpotsPanel(StripWindow(Settings(), tmp_path / "s.json"))
    p.apply([SpotSummary("Monkey Swamp I", "Monkey Swamp I", 98_200, 2520, datetime.now(), True, 41_000),
             SpotSummary("Old", "Old", 10_000, 900, datetime.now(), False, None)], None)
    assert [r[2] for r in p.row_texts()] == ["+41K", "—"] and len(p.row_texts()[0]) == 5


def test_strip_subline_single_total(qapp, tmp_path):
    import re
    pat = r"^Mesos [+−-]?[\d.]+K? · (—|[+−-]?[\d.]+K?)/h$"
    for kw in (dict(mesos_net=12_400, mesos_per_hour=41_000), dict(mesos_net=12_400, mesos_per_hour=None)):
        t = _meso_strip(tmp_path, **kw).meso_text()
        assert re.match(pat, t), t
        assert "picked up" not in t and "potions" not in t
    assert _meso_strip(tmp_path, mesos_net=1, mesos_per_hour=None).meso_text().endswith(" · —/h")


def test_minimize_hides_strip_and_panel_then_restores(qapp, tmp_path):
    w = StripWindow(Settings(), tmp_path / "s.json")
    p = SpotsPanel(w)
    w.show()
    w._toggle_panel()
    assert p.isVisible()
    w._min_btn.click()
    assert w.is_minimized() and not w.isVisible() and not p.isVisible()
    w.toggle_minimized()
    assert not w.is_minimized() and w.isVisible() and p.isVisible()
    w.close(); p.close()


def test_restore_keeps_closed_panel_closed(qapp, tmp_path):
    w = StripWindow(Settings(), tmp_path / "s.json")
    p = SpotsPanel(w)
    w.show()
    w.minimize()
    w.restore()
    assert w.isVisible() and not p.isVisible()
    w.close()


def test_strip_share_toggle_persists_and_signals(qapp, tmp_path):
    s = Settings()
    w = StripWindow(s, tmp_path / "s.json")
    seen = []
    w.shared_changed.connect(seen.append)
    assert not w.is_shared()
    w.set_shared(True)
    assert w.is_shared() and s.show_in_share and seen == [True]
    assert '"show_in_share": true' in (tmp_path / "s.json").read_text()


class _FakeEngine:
    def __init__(self, zones):
        self.zones = zones

    def read_zones(self):
        return self.zones


class _FakeThread:
    def __init__(self, engine):
        self.engine = engine


def test_tray_warns_once_when_shared_strip_covers_a_read_zone(qapp, tmp_path):
    from expstrip.share import STATUS_BAR
    from expstrip.ui.tray import Tray
    from expstrip.vision.layout import Rect
    strip = StripWindow(Settings(), tmp_path / "s.json")
    tray = Tray(qapp, strip, _FakeThread(_FakeEngine(((STATUS_BAR, Rect(0, 700, 1000, 100)),))))
    msgs = []
    tray.icon.showMessage = lambda title, *a: msgs.append(title)
    tray.overlay_rects = lambda: [Rect(100, 720, 640, 48)]
    assert tray.check_share_overlap() == []          # not shared: no check
    tray.toggle_share()
    assert strip.is_shared() and tray.share_action.isChecked()
    assert tray.check_share_overlap() == [STATUS_BAR]
    tray.check_share_overlap()                        # still covered: no second warning
    assert msgs == ["ExpStrip is visible in screen share", "ExpStrip covers part of the game"]
    assert "covers game" in tray.share_action.text()
    tray.overlay_rects = lambda: [Rect(100, 0, 640, 48)]
    assert tray.check_share_overlap() == [] and tray.share_action.text() == "Show in screen share"
    tray.toggle_share()
    assert not strip.is_shared() and not tray.share_action.isChecked()
    tray.icon.hide()
