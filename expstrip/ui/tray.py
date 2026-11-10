"""System tray icon and menu."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from expstrip.paths import data_dir
from expstrip.share import covered
from expstrip.ui import theme
from expstrip.ui.strip import StripWindow
from expstrip.vision.layout import Rect

SHARE_CHECK_MS = 2000


def window_rect(widget) -> Rect | None:
    """Screen rect of a shown widget in physical pixels (the space the game capture uses)."""
    if widget is None or not widget.isVisible():
        return None
    import win32gui
    try:
        l, t, r, b = win32gui.GetWindowRect(int(widget.winId()))
    except Exception:
        return None
    return Rect(l, t, r - l, b - t)


def make_icon() -> QIcon:
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ORANGE))
    p.drawEllipse(1, 1, 30, 30)
    p.setPen(QColor("#ffffff"))
    p.setFont(theme.font(14, QFont.Weight.ExtraBold))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "XP")
    p.end()
    return QIcon(pm)


class Tray:
    """Tray menu. The engine is taken from engine_thread.engine; pause/resume run on the UI thread."""

    def __init__(self, app, strip: StripWindow, engine_thread, panel=None) -> None:
        self._app = app
        self._strip = strip
        self._engine = engine_thread.engine
        self._paused = False
        self._panel = panel
        self._covered: list[str] = []
        self.icon = QSystemTrayIcon(make_icon(), app)
        self.icon.setToolTip("ExpStrip")
        self.menu = QMenu()
        self.show_action = QAction(self.menu)
        self.lock_action = QAction(self.menu)
        self.pause_action = QAction(self.menu)
        self.share_action = QAction("Show in screen share", self.menu)
        self.share_action.setCheckable(True)
        reset_action = QAction("Reset session", self.menu)
        data_action = QAction("Open data folder", self.menu)
        quit_action = QAction("Quit", self.menu)
        self.menu.addAction(self.show_action)
        self.menu.addSeparator()
        for a in (self.lock_action, self.share_action, self.pause_action, reset_action,
                  data_action, quit_action):
            self.menu.addAction(a)
        self.show_action.triggered.connect(strip.toggle_minimized)
        self.lock_action.triggered.connect(self.toggle_lock)
        self.pause_action.triggered.connect(self.toggle_pause)
        self.share_action.triggered.connect(self.toggle_share)
        reset_action.triggered.connect(self.reset_session)
        data_action.triggered.connect(lambda: os.startfile(data_dir()))
        quit_action.triggered.connect(app.quit)
        strip.pause_toggled.connect(self.toggle_pause)
        strip.reset_requested.connect(self.reset_session)
        strip.locked_changed.connect(lambda _l: self._refresh())
        strip.minimized_changed.connect(self._on_minimized)
        self.icon.activated.connect(self._on_activated)
        self._hinted = False
        self._share_timer = QTimer()
        self._share_timer.setInterval(SHARE_CHECK_MS)
        self._share_timer.timeout.connect(self.check_share_overlap)
        if strip.is_shared():
            self._share_timer.start()
        self.icon.setContextMenu(self.menu)
        self._refresh()
        self.icon.show()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # plain left click
            self._strip.toggle_minimized()

    def _on_minimized(self, minimized: bool) -> None:
        self._refresh()
        if minimized and not self._hinted:  # once per run, so nobody thinks it quit
            self._hinted = True
            self.icon.showMessage("ExpStrip is still tracking",
                                  "Click the tray icon to bring the strip back.",
                                  QSystemTrayIcon.MessageIcon.NoIcon, 4000)

    def toggle_lock(self) -> None:
        self._strip.set_locked(not self._strip.is_locked())

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._engine.pause()
        else:
            self._engine.resume()
        self._refresh()

    def toggle_share(self) -> None:
        shared = not self._strip.is_shared()
        self._strip.set_shared(shared)
        self._covered = []
        if shared:
            self._share_timer.start()
            self.icon.showMessage(
                "ExpStrip is visible in screen share",
                "Share your whole screen (not the game window) in Discord. Keep the strip off "
                "the game's status bar, quick slots and pickup lines, or they can't be read.",
                QSystemTrayIcon.MessageIcon.NoIcon, 6000)
        else:
            self._share_timer.stop()
        self._refresh()

    def overlay_rects(self) -> list[Rect]:
        rects = [window_rect(self._strip), window_rect(self._panel)]
        return [r for r in rects if r is not None]

    def check_share_overlap(self) -> list[str]:
        """While shared, warn once each time an overlay starts covering a game area we read."""
        if not self._strip.is_shared():
            self._covered = []
            return []
        names = covered(self.overlay_rects(), list(self._engine.read_zones()))
        new = [n for n in names if n not in self._covered]
        self._covered = names
        if new:
            self.icon.showMessage(
                "ExpStrip covers part of the game",
                f"ExpStrip is over the {', '.join(new)}. Move it, or those values can't be "
                "read while it is visible in screen share.",
                QSystemTrayIcon.MessageIcon.Warning, 6000)
        self._refresh()
        return names

    def reset_session(self) -> None:
        self._engine.reset()

    def _refresh(self) -> None:
        self.show_action.setText("Show strip" if self._strip.is_minimized() else "Hide strip")
        self.lock_action.setText("Unlock" if self._strip.is_locked() else "Lock (click-through)")
        self.pause_action.setText("Resume" if self._paused else "Pause")
        self.share_action.setChecked(self._strip.is_shared())
        self.share_action.setText("Show in screen share ⚠ covers game" if self._covered
                                  else "Show in screen share")
