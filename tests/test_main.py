import threading

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from expstrip.__main__ import parse_args, run_selftest, wire_ui
from expstrip.tracker import Stats
from expstrip.worker import EngineThread, Snapshot, StatusKind


def test_main_parses_debug_flag():
    assert parse_args(["--debug"]).debug is True
    assert parse_args([]).debug is False
    assert parse_args(["--selftest"]).selftest is True
    assert parse_args([]).selftest is False


def _snap():
    st = Stats(level=1, pct=None, map=None, exp_per_hour=None, level_up_in=None,
               last5=0, session=0, paused=False)
    return Snapshot(StatusKind.OK, st, [])


class _FakeEngine:
    def __init__(self):
        self.emitted_from = None

    def tick(self):
        self.emitted_from = QThread.currentThread()
        return _snap()


def test_snapshots_are_applied_on_the_ui_thread(qapp):
    seen = {}

    class Strip:
        def apply(self, snap):
            seen["strip"] = QThread.currentThread()

    class Panel:
        def apply_snapshot(self, snap):
            seen["panel"] = QThread.currentThread()

    # the slot owners must be QObjects for queued delivery; use the real classes' pattern
    from PySide6.QtCore import QObject, Slot

    class StripObj(QObject):
        @Slot(object)
        def apply(self, snap):
            seen["strip"] = QThread.currentThread()

    class PanelObj(QObject):
        @Slot(object)
        def apply_snapshot(self, snap):
            seen["panel"] = QThread.currentThread()

    eng = _FakeEngine()
    et = EngineThread(eng, interval_ms=50)
    strip, panel = StripObj(), PanelObj()
    wire_ui(et, strip, panel)
    et.start()
    try:
        for _ in range(100):
            QApplication.processEvents()
            if len(seen) == 2:
                break
            QThread.msleep(20)
    finally:
        et.requestInterruption()
        et.wait(3000)
    assert eng.emitted_from is not QApplication.instance().thread()
    assert seen["strip"] == qapp.thread()
    assert seen["panel"] == qapp.thread()


def test_selftest_passes_in_dev(tmp_path):
    # the offscreen platform has no fonts, so run it in a child process on the real platform
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    out = tmp_path / "selftest.txt"
    code = chr(10).join([
        "import sys",
        "from PySide6.QtWidgets import QApplication",
        "from expstrip.__main__ import run_selftest",
        "app = QApplication([])",
        "ok, msg = run_selftest(sys.argv[1])",
        "print(msg)",
        "sys.exit(0 if ok else 1)",
    ])
    r = subprocess.run([sys.executable, "-c", code, str(out)], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.read_text(encoding="utf-8").startswith("selftest OK")
