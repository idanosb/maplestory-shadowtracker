"""The always-on-top strip: one dark slim line (level, EXP/h, level-up time, last 5 min, mesos, buttons) over a thin progress line."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPolygonF, QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

from expstrip.config import Settings, save_settings
from expstrip.ui import theme
from expstrip.ui.format import EM_DASH, format_duration, format_k
from expstrip.win.affinity import set_capture_excluded
from expstrip.win.overlay import set_click_through, set_no_activate
from expstrip.worker import Snapshot, StatusKind

log = logging.getLogger(__name__)

STRIP_W, STRIP_H = 640, 48
CORNER_RADIUS = 10
TRACK_H = 4
RESET_CONFIRM_MS = 3000

STATUS_TEXT = {
    StatusKind.WAITING: "Waiting for game…",
    StatusKind.UNREADABLE: "Can't read EXP — is the status bar visible?",
    StatusKind.PAUSED: "Paused",
    StatusKind.NOT_VISIBLE: "Game not visible — use windowed mode",
}


def rounded_region(w: int, h: int, radius: float) -> QRegion:
    """Rounded-rect mask. Used instead of per-pixel transparency, which breaks capture exclusion."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    return QRegion(path.toFillPolygon().toPolygon())


def paint_card(widget: QWidget, radius: float) -> QPainter:
    """Dark background; returns the painter, clipped to the rounded card, for extra drawing."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(widget.rect())
    p.fillRect(r, QColor(theme.BG))
    clip = QPainterPath()
    clip.addRoundedRect(r, radius, radius)
    p.setClipPath(clip)
    return p


def stroke_card(p: QPainter, widget: QWidget, radius: float) -> None:
    """1px outline around the card."""
    p.setClipping(False)
    p.setPen(QPen(QColor(theme.BORDER), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def apply_overlay_flags(widget: QWidget) -> None:
    widget.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                          | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)


def _label(text: str, px: int, color: str, weight: QFont.Weight = QFont.Weight.Normal) -> QLabel:
    lab = QLabel(text)
    lab.setFont(theme.font(px, weight))
    lab.setStyleSheet(f"color:{color};background:transparent;")
    lab.setTextFormat(Qt.TextFormat.PlainText)
    return lab


class _Glyph(QWidget):
    """Small muted icon in front of a value: "clock" or "coin"."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self.setFixedSize(16, 16)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.width() / 14, self.height() / 14)
        pen = QPen(QColor(theme.MUTED), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(QRectF(1.5, 1.5, 11, 11))
        c = QPointF(7, 7)
        if self.kind == "clock":
            p.drawLine(c, QPointF(7, 3.8))
            p.drawLine(c, QPointF(9.4, 8.4))
        else:
            p.setFont(theme.font(9, QFont.Weight.Bold))
            p.drawText(QRectF(0, 0, 14, 14), Qt.AlignmentFlag.AlignCenter, "$")


class _IconButton(QAbstractButton):
    """Flat button whose glyph is painted (emoji fonts would ignore our colors)."""

    SIZE = 28

    def __init__(self, kind: str, tip: str) -> None:
        super().__init__()
        self.kind = kind  # "pause" | "menu" | "reset" | "minimize"
        self.active = False
        self.setToolTip(tip)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def sizeHint(self) -> QSize:
        return QSize(self.SIZE, self.SIZE)

    def set_active(self, on: bool) -> None:
        self.active = on
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self.underMouse():
            p.setBrush(QColor(theme.HOVER))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.scale(self.SIZE / 24, self.SIZE / 24)
        color = QColor(theme.ORANGE if self.active else theme.MUTED)
        p.setBrush(color)
        cx, cy = 12.0, 12.0
        if self.kind == "reset":
            self._paint_reset(p, color, cx, cy)
        elif self.kind == "minimize":
            p.drawRoundedRect(QRectF(cx - 5.5, cy - 0.9, 11, 1.8), 0.9, 0.9)
        elif self.kind == "menu":  # list: three dots + three lines
            for dy in (-4, 0, 4):
                p.drawEllipse(QPointF(cx - 5, cy + dy), 1.1, 1.1)
                p.drawRoundedRect(QRectF(cx - 2.5, cy + dy - 0.9, 8.5, 1.8), 0.9, 0.9)
        elif self.active:  # paused: show play triangle
            p.drawPolygon(QPolygonF([QPointF(cx - 3.5, cy - 5.5), QPointF(cx - 3.5, cy + 5.5), QPointF(cx + 5, cy)]))
        else:
            p.drawRoundedRect(QRectF(cx - 4.5, cy - 5.5, 3, 11), 0.8, 0.8)
            p.drawRoundedRect(QRectF(cx + 1.5, cy - 5.5, 3, 11), 0.8, 0.8)

    @staticmethod
    def _paint_reset(p: QPainter, color: QColor, cx: float, cy: float) -> None:
        """Circular arrow: an open ring with an arrowhead at its end."""
        pen = QPen(color, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = 5.0
        p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), 60 * 16, 270 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(cx + r - 3, cy - 0.5), QPointF(cx + r + 3, cy - 0.5),
                                 QPointF(cx + r, cy + 3.5)]))


class StripWindow(QWidget):
    pause_toggled = Signal()
    reset_requested = Signal()
    panel_toggled = Signal(bool)
    moved = Signal()
    locked_changed = Signal(bool)
    minimized_changed = Signal(bool)
    shared_changed = Signal(bool)

    def __init__(self, settings: Settings, settings_path: Path) -> None:
        super().__init__()
        self._settings = settings
        self._path = Path(settings_path)
        self._drag: QPoint | None = None
        self._panel_open = False
        self._minimized = False
        self._locked = bool(settings.locked)
        self._shared = bool(settings.show_in_share)
        self._fraction = 0.0
        self._texts = [EM_DASH] * 4  # EXP/hour, level up in, 5 min, session
        self._meso_rest = ""
        self.affinity_ok = False
        self.no_activate_ok = False
        self._press_pos = None
        self.click_through_ok = False
        apply_overlay_flags(self)
        self.setFixedSize(STRIP_W, STRIP_H)
        self.setMask(rounded_region(STRIP_W, STRIP_H, CORNER_RADIUS))
        self.setWindowTitle("ExpStrip")
        self._build()
        self._restore_position()

    # -- construction ------------------------------------------------
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 0, 9, TRACK_H)
        root.setSpacing(16)

        self._level = _label(f"Lv {EM_DASH}", 16, theme.PEACH, QFont.Weight.DemiBold)
        root.addWidget(self._level)

        # values: EXP/h · clock level-up time · 5m gain · coin net mesos
        self._ring = QWidget()
        ring = QHBoxLayout(self._ring)
        ring.setContentsMargins(0, 0, 0, 0)
        ring.setSpacing(16)
        self._rate = _label(EM_DASH, 20, theme.WHITE, QFont.Weight.DemiBold)
        self._rate.setToolTip("EXP/hour at your pace over the last 5 minutes of play on this map")
        ring.addLayout(self._pair(self._rate, _label("/h", 12, theme.MUTED), spacing=1))
        self._lvl_up = _label(EM_DASH, 16, theme.TEXT)
        ring.addLayout(self._pair(_Glyph("clock"), self._lvl_up))
        self._last5 = _label(EM_DASH, 16, theme.TEXT)
        self._last5.setToolTip("EXP gained in the last 5 minutes")
        ring.addLayout(self._pair(_label("5m", 12, theme.MUTED), self._last5))
        self._meso = QWidget()
        self._meso_net = _label(EM_DASH, 16, theme.MUTED, QFont.Weight.DemiBold)
        self._meso.setLayout(self._pair(_Glyph("coin"), self._meso_net))
        ring.addWidget(self._meso)
        root.addWidget(self._ring)

        self._status = _label("", 15, theme.MUTED)
        self._status.hide()
        root.addWidget(self._status)
        root.addStretch(1)

        side = QHBoxLayout()
        side.setSpacing(2)
        self._pause_btn = _IconButton("pause", "Pause / resume")
        self.reset_button = _IconButton("reset", "Reset session")
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.setInterval(RESET_CONFIRM_MS)
        self._reset_timer.timeout.connect(self._clear_reset_confirm)
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self._menu_btn = _IconButton("menu", "Training spots")
        self._min_btn = _IconButton("minimize", "Minimize — click the tray icon to bring it back")
        self._pause_btn.clicked.connect(lambda: self.pause_toggled.emit())
        self._menu_btn.clicked.connect(self._toggle_panel)
        self._min_btn.clicked.connect(self.minimize)
        for b in (self._pause_btn, self._menu_btn, self.reset_button, self._min_btn):
            side.addWidget(b)
        root.addLayout(side)

    @staticmethod
    def _pair(first: QWidget, second: QWidget, spacing: int = 6) -> QHBoxLayout:
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(spacing)
        box.addWidget(first, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addWidget(second, 0, Qt.AlignmentFlag.AlignVCenter)
        return box

    # -- painting ----------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = paint_card(self, CORNER_RADIUS)
        y = self.height() - TRACK_H
        p.fillRect(QRectF(0, y, self.width(), TRACK_H), QColor(theme.TRACK))
        w = self.width() * max(0.0, min(1.0, self._fraction))
        if w > 0:
            p.fillRect(QRectF(0, y, w, TRACK_H), QColor(theme.ORANGE))
        stroke_card(p, self, CORNER_RADIUS)

    # -- data --------------------------------------------------------
    @Slot(object)
    def apply(self, snap: Snapshot) -> None:
        st = snap.stats
        if st.level is not None:
            self._level.setText(f"Lv {st.level}")
        if snap.status == StatusKind.OK:
            if st.pct is not None:
                self._fraction = st.pct / 100.0
                self.update()
            self._texts = [format_k(st.exp_per_hour), format_duration(st.level_up_in),
                           format_k(st.last5, signed=True), format_k(st.session, signed=True)]
            self._rate.setText(self._texts[0])
            self._lvl_up.setText(self._texts[1])
            self._last5.setText(self._texts[2])
            self._set_meso(st)
            self.setToolTip(f"Level up in {self._texts[1]}  ·  last 5 min {self._texts[2]}"
                            f"  ·  session {self._texts[3]}\n{self.meso_text()}")
            self._status.setText("")
            self._status.hide()
            self._ring.show()
        else:
            self._status.setText(STATUS_TEXT.get(snap.status, ""))
            self._ring.hide()
            self._status.show()
            self.setToolTip("")
        self._pause_btn.set_active(snap.status == StatusKind.PAUSED)

    def _set_meso(self, st) -> None:
        net = st.mesos_net
        color = theme.POSITIVE if net > 0 else theme.NEGATIVE if net < 0 else theme.MUTED
        self._meso_net.setText(format_k(net, signed=True))
        self._meso_net.setStyleSheet(f"color:{color};background:transparent;")
        self._meso_rest = f" · {format_k(st.mesos_per_hour)}/h"

    def meso_text(self) -> str:
        """Full meso line as the tooltip shows it (the strip itself shows only the net)."""
        return "Mesos " + self._meso_net.text() + self._meso_rest

    def meso_net_style(self) -> str:
        return self._meso_net.styleSheet()

    # -- reset (two-click confirm) -----------------------------------
    def _on_reset_clicked(self) -> None:
        if self.reset_button.active:
            self._clear_reset_confirm()
            self.reset_requested.emit()
        else:
            self.reset_button.set_active(True)
            self.reset_button.setToolTip("Click again to reset")
            self._reset_timer.start()

    def _clear_reset_confirm(self) -> None:
        self._reset_timer.stop()
        self.reset_button.set_active(False)
        self.reset_button.setToolTip("Reset session")

    def value_texts(self) -> list[str]:
        return list(self._texts)

    def level_text(self) -> str:
        return self._level.text().removeprefix("Lv ")

    def status_text(self) -> str:
        return self._status.text()

    # -- panel / lock ------------------------------------------------
    def _toggle_panel(self) -> None:
        self._panel_open = not self._panel_open
        self._menu_btn.set_active(self._panel_open)
        self.panel_toggled.emit(self._panel_open)

    def is_locked(self) -> bool:
        return self._locked

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self._settings.locked = locked
        self._persist()
        if self.isVisible():
            self.click_through_ok = set_click_through(int(self.winId()), locked)
            if not self.click_through_ok:
                log.warning("click-through toggle failed")
        self.locked_changed.emit(locked)

    # -- screen share ------------------------------------------------
    def is_shared(self) -> bool:
        """True when the strip (and panel) are visible in screen shares / recordings."""
        return self._shared

    def set_shared(self, shared: bool) -> None:
        self._shared = shared
        self._settings.show_in_share = shared
        self._persist()
        if self.isVisible():
            self._apply_affinity()
        self.shared_changed.emit(shared)

    def _apply_affinity(self) -> None:
        self.affinity_ok = set_capture_excluded(int(self.winId()), not self._shared)
        if not self.affinity_ok:
            log.warning("display affinity change failed for strip")

    # -- minimize (hide to tray; tracking keeps running) ---------------
    def is_minimized(self) -> bool:
        return self._minimized

    def minimize(self) -> None:
        if self._minimized:
            return
        self._minimized = True
        self._clear_reset_confirm()
        if self._panel_open:
            self.panel_toggled.emit(False)  # hide the panel but remember it was open
        self.hide()
        self.minimized_changed.emit(True)

    def restore(self) -> None:
        if not self._minimized:
            return
        self._minimized = False
        self.show()
        if self._panel_open:
            self.panel_toggled.emit(True)
        self.minimized_changed.emit(False)

    def toggle_minimized(self) -> None:
        self.restore() if self._minimized else self.minimize()

    # -- window events -----------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        hwnd = int(self.winId())
        self.no_activate_ok = set_no_activate(hwnd)
        self._apply_affinity()
        if self._locked:
            self.click_through_ok = set_click_through(hwnd, True)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self.moved.emit()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is not None:
            self._drag = None
            if self.pos() != self._press_pos:
                self._save_position()

    def _save_position(self) -> None:
        self._settings.strip_x = self.x()
        self._settings.strip_y = self.y()
        self._persist()

    def _persist(self) -> None:
        try:
            save_settings(self._settings, self._path)
        except OSError as e:
            log.warning("could not save settings: %s", e)

    def _restore_position(self) -> None:
        s = self._settings
        if s.strip_x is not None and s.strip_y is not None and self._on_a_screen(s.strip_x, s.strip_y):
            self.move(s.strip_x, s.strip_y)
            return
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        self.move(g.x() + (g.width() - STRIP_W) // 2, g.y() + 8)

    @staticmethod
    def _on_a_screen(x: int, y: int) -> bool:
        """True if the strip placed at (x, y) overlaps some current screen by a usable amount."""
        want = QRectF(x, y, STRIP_W, STRIP_H)
        for scr in QGuiApplication.screens():
            hit = want.intersected(QRectF(scr.availableGeometry()))
            if hit.width() >= 100 and hit.height() >= STRIP_H / 2:
                return True
        return False
