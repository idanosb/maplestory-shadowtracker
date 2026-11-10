"""Training-spots table that sits under the strip."""
from __future__ import annotations

import html
import logging
from datetime import datetime

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from expstrip.history import SpotSummary
from expstrip.reading import MapName
from expstrip.ui import theme
from expstrip.ui.format import format_duration, format_k
from expstrip.ui.strip import StripWindow, apply_overlay_flags, paint_card, rounded_region, stroke_card
from expstrip.win.affinity import set_capture_excluded
from expstrip.win.overlay import set_click_through, set_no_activate

log = logging.getLogger(__name__)

GAP = 6
MARGIN_X = 10
RADIUS = 10
MAX_BAR = 92
EMPTY = "No spots yet — train for a minute somewhere."
NOW = " · now"


def format_last(when: datetime, now: datetime | None = None) -> str:
    today = (now or datetime.now()).date()
    days = (today - when.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{when.strftime('%b')} {when.day}"


class _Bar(QWidget):
    def __init__(self, width: int) -> None:
        super().__init__()
        self.setFixedSize(max(2, width), 7)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BAR))
        p.drawRoundedRect(self.rect(), 3, 3)


class SpotsPanel(QWidget):
    def __init__(self, strip: StripWindow) -> None:
        super().__init__()
        self._strip = strip
        self._rows: list[tuple[str, str, str, str, str]] = []
        self._empty = ""
        apply_overlay_flags(self)
        self.setWindowTitle("ExpStrip spots")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        head = QLabel("Training spots")
        head.setFont(theme.font(13, QFont.Weight.DemiBold))
        head.setStyleSheet(f"color:{theme.MUTED};")
        root.addWidget(head)
        self._body = QWidget()
        self._grid = QGridLayout(self._body)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(0)
        root.addWidget(self._body)
        self.setFixedWidth(strip.width() - 2 * MARGIN_X)
        strip.moved.connect(self._follow)
        strip.panel_toggled.connect(self.setVisible)
        strip.locked_changed.connect(self._on_locked)
        strip.shared_changed.connect(self._on_shared)
        self.apply([], None)

    # -- data --------------------------------------------------------
    @Slot(object)
    def apply_snapshot(self, snap) -> None:
        self.apply(snap.summaries, snap.stats.map)

    def apply(self, summaries: list[SpotSummary], current: MapName | None) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._rows = []
        self._empty = ""
        if not summaries:
            self._empty = EMPTY
            lab = QLabel(EMPTY)
            lab.setFont(theme.font(14))
            lab.setStyleSheet(f"color:{theme.MUTED};")
            self._grid.addWidget(lab, 0, 0)
        else:
            for c, name in enumerate(("Map", "EXP/hour", "", "Mesos/hour", "Time", "Last")):
                h = QLabel(name)
                h.setFont(theme.font(12))
                h.setStyleSheet(f"color:{theme.MUTED};")
                self._grid.addWidget(h, 0, c)
            top = max(s.exp_per_hour for s in summaries) or 1.0
            now = datetime.now()
            for r, s in enumerate(summaries, start=1):
                is_now = current is not None and s.map_full == current.full
                name = ("🏆 " if s.is_best else "") + s.spot + (NOW if is_now else "")
                mph = s.mesos_per_hour
                texts = (name, format_k(s.exp_per_hour), format_k(mph, signed=True),
                         format_duration(s.total_seconds), format_last(s.last_visit, now))
                meso_color = (theme.MUTED if not mph
                              else theme.POSITIVE if mph > 0 else theme.NEGATIVE)
                self._rows.append(texts)
                w = QFont.Weight.DemiBold if s.is_best else QFont.Weight.Normal
                rate_color = theme.PEACH if s.is_best else theme.TEXT
                self._grid.addWidget(self._cell(texts[0], w, theme.WHITE if s.is_best else theme.TEXT, is_now), r, 0)
                self._grid.addWidget(self._cell(texts[1], w, rate_color), r, 1)
                self._grid.addWidget(_Bar(round(MAX_BAR * s.exp_per_hour / top)), r, 2)
                self._grid.addWidget(self._cell(texts[2], w, meso_color), r, 3)
                self._grid.addWidget(self._cell(texts[3], w, theme.TEXT), r, 4)
                self._grid.addWidget(self._cell(texts[4], w, theme.TEXT), r, 5)
            self._grid.setColumnStretch(0, 1)
        self.adjustSize()
        self._resize_mask()
        self._follow()

    @staticmethod
    def _cell(text: str, weight: QFont.Weight, color: str, now: bool = False) -> QLabel:
        if now:
            base = html.escape(text[:-len(NOW)])
            lab = QLabel(f"{base}<span style='color:{theme.ORANGE};font-size:12px'>{NOW}</span>")
            lab.setTextFormat(Qt.TextFormat.RichText)
        else:
            lab = QLabel(text)
            lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setFont(theme.font(14, weight))
        lab.setStyleSheet(f"color:{color};background:transparent;border-top:1px solid {theme.BORDER};padding:6px 0;")
        return lab

    def row_texts(self) -> list[tuple[str, str, str, str, str]]:
        return list(self._rows)

    def display_names(self) -> list[str]:
        """Name column as the user sees it (rich text rendered to plain)."""
        from PySide6.QtGui import QTextDocument
        out = []
        for lab in self._body.findChildren(QLabel):
            if lab.textFormat() == Qt.TextFormat.RichText:
                d = QTextDocument()
                d.setHtml(lab.text())
                out.append(d.toPlainText())
        return out

    def empty_text(self) -> str:
        return self._empty

    # -- window ------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = paint_card(self, RADIUS)
        stroke_card(p, self, RADIUS)

    def _resize_mask(self) -> None:
        self.setMask(rounded_region(self.width(), self.height(), RADIUS))

    def _follow(self) -> None:
        self.move(self._strip.x() + MARGIN_X, self._strip.y() + self._strip.height() + GAP)

    def _on_locked(self, locked: bool) -> None:
        if self.isVisible():
            set_click_through(int(self.winId()), locked)

    def _on_shared(self, _shared: bool) -> None:
        if self.isVisible():
            self._apply_affinity()

    def _apply_affinity(self) -> None:
        if not set_capture_excluded(int(self.winId()), not self._strip.is_shared()):
            log.warning("display affinity change failed for panel")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.adjustSize()
        self._resize_mask()
        self._follow()
        hwnd = int(self.winId())
        self.no_activate_ok = set_no_activate(hwnd)
        self._apply_affinity()
        if self._strip.is_locked():
            set_click_through(hwnd, True)
