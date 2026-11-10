"""EXP Strip entry point."""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from expstrip.paths import app_dir, data_dir, templates_dir

MUTEX_NAME = "ExpStripSingleInstance"
ERROR_ALREADY_EXISTS = 183


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="ExpStrip")
    ap.add_argument("--debug", action="store_true", help="save debug crops to data/debug")
    ap.add_argument("--selftest", action="store_true", help="check bundled files and OCR, then exit")
    return ap.parse_args(argv)


def _say(text: str) -> None:
    if sys.stdout is not None:
        try:
            print(text)
        except Exception:
            pass


def _check_meso_templates() -> str:
    """Load every meso-tracker template from the bundle; raise if any is missing."""
    import cv2

    from expstrip.vision.glyphs import GlyphSet

    t = templates_dir()
    anchor = cv2.imread(str(t / "anchor_mesos.png"))
    if anchor is None:
        raise FileNotFoundError(t / "anchor_mesos.png")
    prefix = cv2.imread(str(t / "pickup" / "prefix.png"), cv2.IMREAD_GRAYSCALE)
    if prefix is None:
        raise FileNotFoundError(t / "pickup" / "prefix.png")
    pickup_n = len([p for p in (t / "pickup").glob("*.png") if p.stem != "prefix"])
    potions = sorted(p.stem for p in (t / "potions").glob("*.png"))
    for name in ("Meat", "Lemon"):
        if name not in potions:
            raise FileNotFoundError(t / "potions" / f"{name}.png")
    sets = {}
    for folder in ("count", "mesos"):
        gs = sets[folder] = GlyphSet.load(t / folder)
        missing = [c for c in "0123456789" if c not in gs.chars]
        if missing:
            raise RuntimeError(f"{folder} templates missing digits {missing}")
    if not pickup_n:
        raise RuntimeError("no pickup glyph templates")
    items = t / "items"
    for tab in ("Etc", "Use"):
        if not (items / f"suffix_{tab}.png").is_file():
            raise FileNotFoundError(items / f"suffix_{tab}.png")
    name_stems = {re.sub(r"_\d+$", "", p.stem) for p in items.glob("*.png")
                  if not p.stem.startswith("suffix_")
                  and not re.match(r"^(x|qty\d)(_\d+)?$", p.stem)}
    if not name_stems:
        raise RuntimeError("no item name templates")
    return (f"pickup={pickup_n}, potions={len(potions)}, "
            f"count={len(sets['count'].chars)}, mesos={len(sets['mesos'].chars)}, "
            f"items={len(name_stems)}")


def run_selftest(out_path: Path | None = None) -> tuple[bool, str]:
    """Templates load, and Windows OCR reads text drawn by Qt. Needs a QGuiApplication."""
    try:
        import numpy as np
        from PySide6.QtGui import QColor, QFont, QImage, QPainter

        from expstrip.vision.glyphs import GlyphSet
        from expstrip.vision.ocr import ocr_lines

        gs = GlyphSet.load(templates_dir() / "exp")
        if not gs.chars:
            raise RuntimeError(f"no exp templates in {templates_dir() / 'exp'}")
        counts = _check_meso_templates()
        img = QImage(360, 90, QImage.Format.Format_RGB888)
        img.fill(QColor("white"))
        p = QPainter(img)
        p.setPen(QColor("black"))
        f = QFont("Arial")
        f.setPixelSize(48)
        p.setFont(f)
        p.drawText(img.rect(), 0x84, "Henesys 123")  # AlignCenter
        p.end()
        w, h, bpl = img.width(), img.height(), img.bytesPerLine()
        raw = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)
        rgb = raw[:, : w * 3].reshape(h, w, 3)
        text = " ".join(ocr_lines(rgb[:, :, ::-1].copy())).lower()
        if "henesys" not in text:
            raise RuntimeError(f"OCR returned {text!r}")
        ok, msg = True, f"selftest OK (glyph chars={len(gs.chars)}, {counts}, ocr={text!r})"
    except Exception as e:
        ok, msg = False, f"selftest FAILED: {type(e).__name__}: {e}"
    if out_path is not None:
        try:
            Path(out_path).write_text(msg + "\n", encoding="utf-8")
        except OSError:
            pass
    return ok, msg


def wire_ui(engine_thread, strip, panel) -> None:
    """Snapshots are emitted on the worker thread; queue them onto the receivers' (UI) thread."""
    from PySide6.QtCore import Qt
    engine_thread.snapshot_ready.connect(strip.apply, Qt.ConnectionType.QueuedConnection)
    engine_thread.snapshot_ready.connect(panel.apply_snapshot, Qt.ConnectionType.QueuedConnection)


def _acquire_single_instance():
    import win32api
    import win32event
    handle = win32event.CreateMutex(None, False, MUTEX_NAME)
    if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    from PySide6.QtWidgets import QApplication

    if args.selftest:
        app = QApplication([])
        ok, msg = run_selftest(data_dir() / "selftest.txt")
        _say(msg)
        return 0 if ok else 1

    mutex = _acquire_single_instance()
    if mutex is None:
        return 0

    logging.basicConfig(
        filename=str(data_dir() / "expstrip.log"), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from expstrip.config import load_settings
    from expstrip.frame_reader import FrameReader
    from expstrip.history import VisitStore
    from expstrip.ui.spots_panel import SpotsPanel
    from expstrip.ui.strip import StripWindow
    from expstrip.ui.tray import Tray
    from expstrip.worker import Engine, EngineThread

    settings_path = app_dir() / "settings.json"
    settings = load_settings(settings_path)
    store = VisitStore(data_dir())
    try:
        known_maps = [s.map_full for s in store.summaries()]
    except Exception:
        logging.getLogger("expstrip").exception("could not read visit history")
        known_maps = []
    reader = FrameReader(
        debug_dir=data_dir() / "debug" if args.debug else None,
        learned_dir=data_dir() / "glyphs" / "level",
        level_hint=settings.level_hint,
        known_maps=known_maps)
    engine = Engine(settings, store, reader=reader)
    engine.recover_in_progress()

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    thread = EngineThread(engine)
    strip = StripWindow(settings, settings_path)
    panel = SpotsPanel(strip)
    tray = Tray(app, strip, thread, panel)  # connects strip.pause_toggled itself
    wire_ui(thread, strip, panel)

    def stop() -> None:
        thread.requestInterruption()
        thread.wait(3000)
        engine.shutdown()

    app.aboutToQuit.connect(stop)

    # A level hint is for one start only: once it gave the level (and taught the missing
    # digits), drop it from settings.json, or after the next level-up it would be stale.
    from PySide6.QtCore import QTimer
    from expstrip.config import save_settings
    hint_timer = QTimer()

    def clear_used_hint() -> None:
        if reader.hint_used and settings.level_hint is not None:
            settings.level_hint = None
            try:
                save_settings(settings, settings_path)
                logging.getLogger("expstrip").info("level hint used; removed from settings")
            except OSError as e:
                logging.getLogger("expstrip").warning("could not clear level hint: %s", e)
        if reader.hint_used or settings.level_hint is None:
            hint_timer.stop()

    hint_timer.timeout.connect(clear_used_hint)
    hint_timer.start(5000)
    strip.show()
    thread.start()
    code = app.exec()
    del tray, mutex
    return code


if __name__ == "__main__":
    sys.exit(main())
